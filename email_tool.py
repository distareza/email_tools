#!/usr/bin/env python3
"""Email tool — list, read, and send Gmail messages via IMAP/SMTP.

Usage:
  email_tool.py list [count]
  email_tool.py read <uid>
  email_tool.py attachment <uid> <filename>
  email_tool.py send --to <addr> --subject <subj> (--body <text> | --body-file <file>)
                     [--attach file ...] [--compress]
"""

import argparse
import email
import json
import email.header
import email.mime.application
import email.mime.multipart
import email.mime.text
import email.utils
import imaplib
import io
import os
import re
import smtplib
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from docx import Document as DocxDocument
from dotenv import load_dotenv
from pypdf import PdfReader


# ── Credentials ───────────────────────────────────────────────────────────────

def get_credentials() -> tuple[str, str]:
    """Load Gmail credentials from .env file."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print(f"Error: {env_path} not found. Copy .env.example to .env and fill in credentials.")
        sys.exit(1)
    load_dotenv(env_path)
    addr = os.getenv("GMAIL_ADDRESS")
    pw = os.getenv("GMAIL_APP_PASSWORD")
    if not addr or not pw:
        print("Error: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        sys.exit(1)
    return addr, pw


# ── Header / date helpers ─────────────────────────────────────────────────────

def decode_header_value(s: str) -> str:
    """Decode an RFC 2047 encoded header string (e.g. =?UTF-8?B?...?=)."""
    if not s:
        return s
    try:
        return str(email.header.make_header(email.header.decode_header(s)))
    except Exception:
        return s


def parse_date(raw: str) -> str:
    """Format an RFC 2822 date string into a readable local datetime."""
    try:
        tt = email.utils.parsedate_tz(raw)
        if tt is None:
            return raw
        return datetime(*tt[:6]).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw


def _msg_datetime(raw: str) -> datetime | None:
    """Parse a raw RFC 2822 date header into a naive datetime (no tz)."""
    try:
        tt = email.utils.parsedate_tz(raw)
        if tt is None:
            return None
        return datetime(*tt[:6])
    except Exception:
        return None


def _parse_datetime_arg(s: str) -> datetime:
    """Argparse type: accept YYYY-MM-DD or 'YYYY-MM-DD HH:MM'."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid date/datetime '{s}'. Expected YYYY-MM-DD or 'YYYY-MM-DD HH:MM'."
    )


def _imap_date(dt: datetime) -> str:
    """Format a datetime as an IMAP SEARCH date string (DD-Mon-YYYY)."""
    return dt.strftime("%d-%b-%Y")


# ── IMAP response parsing ─────────────────────────────────────────────────────

def extract_msg_bytes(data: list) -> bytes | None:
    """Return the literal message bytes from an IMAP fetch response list."""
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def extract_flags(data: list) -> set[str]:
    """Return the set of IMAP flags from a fetch response list."""
    for item in data:
        raw = item[0] if isinstance(item, tuple) else item
        if isinstance(raw, bytes):
            m = re.search(rb"FLAGS \(([^)]*)\)", raw)
            if m:
                return set(m.group(1).decode().split())
    return set()


# ── Message body helpers ──────────────────────────────────────────────────────

def get_text_body(msg: email.message.Message) -> str:
    """Extract the best readable plain-text body from a message."""
    if msg.is_multipart():
        # Prefer text/plain non-attachment parts
        for part in msg.walk():
            if (part.get_content_type() == "text/plain"
                    and "attachment" not in part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        # Fall back to text/html
        for part in msg.walk():
            if (part.get_content_type() == "text/html"
                    and "attachment" not in part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return "[HTML body]\n" + payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return "(No body)"


def get_attachment_parts(msg: email.message.Message) -> list[tuple[str, email.message.Message]]:
    """Return a list of (decoded_filename, part) for every attachment."""
    parts = []
    for part in msg.walk():
        if "attachment" in part.get("Content-Disposition", ""):
            fname = part.get_filename()
            if fname:
                parts.append((decode_header_value(fname), part))
    return parts


# ── Attachment content readers ────────────────────────────────────────────────

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".log", ".rst",
    ".yaml", ".yml", ".json", ".xml",
    ".html", ".htm", ".ini", ".toml", ".sh",
}


def read_attachment_content(data: bytes, filename: str) -> str:
    """Return the content of an attachment as a displayable string."""
    ext = Path(filename).suffix.lower()

    if ext == ".zip":
        return _read_zip(data, filename)
    if ext in _TEXT_EXTENSIONS:
        return _read_text(data)
    if ext == ".docx":
        return _read_docx(data)
    if ext == ".pdf":
        return _read_pdf(data)
    return f"[Cannot read '{filename}': unsupported file type '{ext}']"


def _read_text(data: bytes) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_zip(data: bytes, filename: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            rows = [
                f"Contents of {filename}:",
                f"{'Name':<50} {'Size (bytes)':>14} {'Modified':<20}",
                "-" * 86,
            ]
            for info in zf.infolist():
                mod = (datetime(*info.date_time).strftime("%Y-%m-%d %H:%M:%S")
                       if info.date_time[0] > 1980 else "-")
                rows.append(f"{info.filename:<50} {info.file_size:>14,} {mod:<20}")
        return "\n".join(rows)
    except Exception as exc:
        return f"[Error reading zip '{filename}': {exc}]"


def _read_docx(data: bytes) -> str:
    try:
        doc = DocxDocument(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as exc:
        return f"[Error reading docx: {exc}]"


def _read_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n--- Page Break ---\n\n".join(pages)
    except Exception as exc:
        return f"[Error reading pdf: {exc}]"


# ── IMAP connection ───────────────────────────────────────────────────────────

def imap_connect(addr: str, pw: str) -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    conn.login(addr, pw)
    conn.select("INBOX")
    return conn


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list(
    addr: str,
    pw: str,
    count: int,
    *,
    subject: str | None = None,
    from_addr: str | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
    on: datetime | None = None,
    read: bool | None = None,
    fmt: str = "json",
) -> None:
    """List the most recent inbox messages matching optional filters."""
    # Build IMAP SEARCH criteria (IMAP has date-level granularity; time filtering is done locally)
    criteria_parts: list[str] = []
    if subject:
        criteria_parts.append(f'SUBJECT "{subject}"')
    if from_addr:
        criteria_parts.append(f'FROM "{from_addr}"')
    if on:
        # IMAP ON matches the calendar day; use SINCE+BEFORE for reliability across servers
        criteria_parts.append(f'SINCE {_imap_date(on)}')
        criteria_parts.append(f'BEFORE {_imap_date(on + timedelta(days=1))}')
    if since:
        criteria_parts.append(f'SINCE {_imap_date(since)}')
    if before:
        # When a time component is present, push IMAP boundary one day forward
        # so that messages on that day are fetched and then filtered locally.
        imap_before = before if not (before.hour or before.minute) else before + timedelta(days=1)
        criteria_parts.append(f'BEFORE {_imap_date(imap_before)}')
    if read is True:
        criteria_parts.append("SEEN")
    elif read is False:
        criteria_parts.append("UNSEEN")
    criteria = " ".join(criteria_parts) if criteria_parts else "ALL"

    conn = imap_connect(addr, pw)
    _, raw_uids = conn.uid("search", None, criteria)
    uids = raw_uids[0].split()
    if not uids:
        print("No messages found.")
        conn.logout()
        return

    rows = []
    for uid in uids[-count:]:
        _, data = conn.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER])")
        flags = extract_flags(data)
        hdr_bytes = extract_msg_bytes(data)
        if hdr_bytes is None:
            continue

        hdr = email.message_from_bytes(hdr_bytes)
        raw_date = hdr.get("Date") or ""
        subject_val = decode_header_value(hdr.get("Subject") or "(No subject)")
        sender = decode_header_value(hdr.get("From") or "(Unknown)")
        date_str = parse_date(raw_date)
        # Detect likely attachments from Content-Type without fetching the body
        has_attachment = "mixed" in (hdr.get("Content-Type") or "").lower()

        rows.append({
            "uid": uid.decode(),
            "subject": subject_val,
            "from": sender,
            "date": date_str,
            "dt": _msg_datetime(raw_date),
            "read": r"\Seen" in flags,
            "att": has_attachment,
        })

    conn.logout()

    # Apply time-of-day filtering locally (IMAP SEARCH operates at date granularity only)
    if on is not None and (on.hour or on.minute):
        # Match the exact minute window
        on_end = on + timedelta(minutes=1)
        rows = [r for r in rows if r["dt"] is not None and on <= r["dt"] < on_end]
    if since is not None and (since.hour or since.minute):
        rows = [r for r in rows if r["dt"] is None or r["dt"] >= since]
    if before is not None and (before.hour or before.minute):
        rows = [r for r in rows if r["dt"] is None or r["dt"] < before]

    if not rows:
        print("No messages found.")
        return

    if fmt == "json":
        _print_list_json(rows)
    else:
        _print_list(rows)


def _print_list_json(rows: list[dict]) -> None:
    output = [
        {
            "uid": m["uid"],
            "from": m["from"],
            "subject": m["subject"],
            "date": m["date"],
            "read": m["read"],
            "attachment": m["att"],
        }
        for m in rows
    ]
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _print_list(rows: list[dict]) -> None:
    if not rows:
        print("No messages to display.")
        return
    w_from = max(len("From"), max(len(m["from"]) for m in rows))
    w_subj = max(len("Subject"), max(len(m["subject"]) for m in rows))
    header = f"{'ID':<8} {'From':<{w_from}} {'Subject':<{w_subj}} {'Date':<20} {'R':<3} {'Att'}"
    sep = "-" * len(header)
    print(header)
    print(sep)
    for m in rows:
        print(
            f"{m['uid']:<8} "
            f"{m['from']:<{w_from}} "
            f"{m['subject']:<{w_subj}} "
            f"{m['date']:<20} "
            f"{'Y' if m['read'] else 'N':<3} "
            f"{'Y' if m['att'] else 'N'}"
        )


def cmd_read(addr: str, pw: str, uid: str) -> None:
    """Print the full message (headers + body) for a given UID and mark it as read."""
    conn = imap_connect(addr, pw)
    _, data = conn.uid("fetch", uid.encode(), "(BODY[])")
    body_bytes = extract_msg_bytes(data)
    conn.logout()

    if not body_bytes:
        print(f"Message UID {uid} not found.")
        return

    msg = email.message_from_bytes(body_bytes)
    print(f"From   : {decode_header_value(msg.get('From', ''))}")
    print(f"To     : {decode_header_value(msg.get('To', ''))}")
    print(f"Date   : {parse_date(msg.get('Date', ''))}")
    print(f"Subject: {decode_header_value(msg.get('Subject', ''))}")
    att_parts = get_attachment_parts(msg)
    if att_parts:
        print(f"Attach : {', '.join(n for n, _ in att_parts)}")
    print("-" * 60)
    print(get_text_body(msg))


def cmd_attachment(addr: str, pw: str, uid: str, filename: str) -> None:
    """Display the content of a named attachment."""
    conn = imap_connect(addr, pw)
    _, data = conn.uid("fetch", uid.encode(), "(BODY.PEEK[])")
    body_bytes = extract_msg_bytes(data)
    conn.logout()

    if not body_bytes:
        print(f"Message UID {uid} not found.")
        return

    msg = email.message_from_bytes(body_bytes)
    att_parts = get_attachment_parts(msg)
    matches = [(n, p) for n, p in att_parts if n == filename]

    if not matches:
        print(f"Attachment '{filename}' not found in message {uid}.")
        available = [n for n, _ in att_parts]
        if available:
            print(f"Available attachments: {', '.join(available)}")
        return

    if len(matches) > 1:
        print(f"Warning: {len(matches)} attachments named '{filename}'; showing first.\n")

    _, part = matches[0]
    print(read_attachment_content(part.get_payload(decode=True), filename))


def cmd_send(
    addr: str,
    pw: str,
    to: str,
    subject: str,
    body: str,
    attachments: list[str] | None,
    compress: bool,
) -> None:
    """Send an email with optional attachments."""
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))

    if attachments:
        if compress:
            buf = io.BytesIO()
            seen: dict[str, int] = {}
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for fpath in attachments:
                    base = Path(fpath).name
                    if base in seen:
                        seen[base] += 1
                        stem, suf = Path(base).stem, Path(base).suffix
                        arc_name = f"{stem}_{seen[base]}{suf}"
                    else:
                        seen[base] = 0
                        arc_name = base
                    zf.write(fpath, arc_name)
            buf.seek(0)
            part = email.mime.application.MIMEApplication(buf.read(), Name="attachments.zip")
            part["Content-Disposition"] = 'attachment; filename="attachments.zip"'
            msg.attach(part)
        else:
            for fpath in attachments:
                p = Path(fpath)
                part = email.mime.application.MIMEApplication(p.read_bytes(), Name=p.name)
                part["Content-Disposition"] = f'attachment; filename="{p.name}"'
                msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(addr, pw)
        smtp.sendmail(addr, to, msg.as_string())
    print(f"Email sent to {to}.")


# ── CLI entry-point ───────────────────────────────────────────────────────────

def main() -> None:
    # Intercept bare "help" before argparse sees it so it always prints usage
    if len(sys.argv) < 2 or sys.argv[1].lower() in ("help", "--help", "-h"):
        sys.argv = [sys.argv[0], "--help"]

    parser = argparse.ArgumentParser(
        prog="email_tool",
        description="Gmail email tool — list, read, and send messages.",
        epilog=(
            "Examples:\n"
            "  email_tool list 20\n"
            "  email_tool read 12345\n"
            "  email_tool attachment 12345 report.pdf\n"
            '  email_tool send --to bob@example.com --subject "Hi" --body "Hello"\n'
            '  email_tool send --to bob@example.com --subject "Report" '
            "--body-file msg.txt --attach data.csv notes.pdf --compress"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="List recent inbox messages")
    p_list.add_argument(
        "count", nargs="?", type=int, default=10,
        help="Number of messages to show (default: 10)",
    )
    p_list.add_argument("--subject", metavar="TEXT",
                        help="Filter: subject contains TEXT (case-insensitive)")
    p_list.add_argument("--from", dest="from_addr", metavar="ADDRESS",
                        help="Filter: sender name/address contains ADDRESS")
    date_grp = p_list.add_mutually_exclusive_group()
    date_grp.add_argument("--on", metavar="DATE", type=_parse_datetime_arg,
                          help="Filter: received on DATE (YYYY-MM-DD) or at minute 'YYYY-MM-DD HH:MM'")
    date_grp.add_argument("--since", metavar="DATE", type=_parse_datetime_arg,
                          help="Filter: received on or after DATE (YYYY-MM-DD or 'YYYY-MM-DD HH:MM')")
    p_list.add_argument("--before", metavar="DATE", type=_parse_datetime_arg,
                        help="Filter: received before DATE (YYYY-MM-DD or 'YYYY-MM-DD HH:MM'); combinable with --since")
    read_grp = p_list.add_mutually_exclusive_group()
    read_grp.add_argument("--read", action="store_true", default=False,
                          help="Filter: only read (seen) messages")
    read_grp.add_argument("--unread", action="store_true", default=False,
                          help="Filter: only unread (unseen) messages")
    p_list.add_argument(
        "--format", dest="format", choices=["table", "json"], default="json",
        help="Output format: table (default) or json",
    )

    # read
    p_read = sub.add_parser("read", help="Read a full message by UID")
    p_read.add_argument("uid", help="Message UID (the ID column from 'list')")

    # attachment
    p_att = sub.add_parser("attachment", help="Display an attachment from a message")
    p_att.add_argument("uid", help="Message UID")
    p_att.add_argument("filename", help="Attachment filename (as shown by 'read')")

    # send
    p_send = sub.add_parser("send", help="Send an email")
    p_send.add_argument("--to", required=True, help="Recipient email address")
    p_send.add_argument("--subject", required=True, help="Email subject")
    body_grp = p_send.add_mutually_exclusive_group(required=True)
    body_grp.add_argument("--body", help="Message body text")
    body_grp.add_argument("--body-file", metavar="FILE", help="File containing message body")
    p_send.add_argument("--attach", nargs="+", metavar="FILE", help="Files to attach")
    p_send.add_argument(
        "--compress", action="store_true",
        help="Compress all attachments into a single zip archive before sending",
    )

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    addr, pw = get_credentials()

    if args.command == "list":
        read_filter = True if args.read else (False if args.unread else None)
        cmd_list(
            addr, pw, args.count,
            subject=args.subject,
            from_addr=args.from_addr,
            on=args.on,
            since=args.since,
            before=args.before,
            read=read_filter,
            fmt=args.format,
        )
    elif args.command == "read":
        cmd_read(addr, pw, args.uid)
    elif args.command == "attachment":
        cmd_attachment(addr, pw, args.uid, args.filename)
    elif args.command == "send":
        body = args.body if args.body else Path(args.body_file).read_text()
        cmd_send(addr, pw, args.to, args.subject, body, args.attach, args.compress)


if __name__ == "__main__":
    main()
