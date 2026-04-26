# email_tool

A command-line Gmail client that lets you **list**, **read**, **download attachments**, and **send** email — all from the terminal.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.12 |
| pyenv (recommended) | any |
| Gmail account with IMAP enabled | — |
| Gmail App Password | — |

---

## Installation

### 1. Install Python 3.12 via pyenv

```bash
# Install pyenv (if not already installed)
curl https://pyenv.run | bash

# Install Python 3.12
pyenv install 3.12.11
```

### 2. Clone / copy the project

```bash
cd /path/to/mail_utils
```

### 3. Create a virtual environment with Python 3.12

```bash
~/.pyenv/versions/3.12.11/bin/python3.12 -m venv .venv
```

### 4. Activate the virtual environment

```bash
# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

### 5. Install dependencies

```bash
pip install -r requirements.txt
```

**Dependencies installed:**

| Package | Purpose |
|---|---|
| `python-dotenv` | Load credentials from `.env` |
| `python-docx` | Read `.docx` attachments |
| `pypdf` | Read `.pdf` attachments |

---

## Gmail Setup

### Enable IMAP

1. Open Gmail → **Settings** (⚙) → **See all settings**
2. Go to the **Forwarding and POP/IMAP** tab
3. Under *IMAP access*, select **Enable IMAP**
4. Click **Save Changes**

### Create an App Password

> Required if your account uses 2-Step Verification (recommended).

1. Go to your [Google Account](https://myaccount.google.com/)
2. Navigate to **Security** → **2-Step Verification** → scroll down to **App passwords**
3. Select app: **Mail**, device: **Other (custom name)** → type `email_tool`
4. Click **Generate** and copy the 16-character password

---

## Configuration

Copy the example file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```ini
GMAIL_ADDRESS=your.address@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

> **Never commit `.env` to version control.** It is already listed in `.gitignore`.

---

## Usage

```
python email_tool.py <command> [options]
```

Run without arguments (or with `help`) to see the full usage summary:

```bash
python email_tool.py
python email_tool.py help
python email_tool.py -h
```

---

### `list` — List inbox messages

```bash
python email_tool.py list [count]
```

| Argument | Default | Description |
|---|---|---|
| `count` | `10` | Number of most-recent messages to show |

**Output columns:**

| Column | Description |
|---|---|
| `ID` | IMAP UID — use this in `read` / `attachment` commands |
| `From` | Sender name / address |
| `Subject` | Email subject |
| `Date` | Received date |
| `R` | `Y` = read, `N` = unread |
| `Att` | `Y` = likely has attachments |

**Example:**

```bash
python email_tool.py list 20
```

---

### `read` — Read a full message

```bash
python email_tool.py read <uid>
```

Prints the sender, recipient, date, subject, attachment list, and body of the message.

**Example:**

```bash
python email_tool.py read 12345
```

---

### `attachment` — Display an attachment

```bash
python email_tool.py attachment <uid> <filename>
```

Reads the attachment and displays its content as text. Supported formats:

| Format | Output |
|---|---|
| `.txt` `.md` `.csv` `.log` `.json` `.yaml` `.xml` `.html` `.sh` `.toml` `.ini` `.rst` | Displayed as plain text |
| `.docx` | Text extracted from paragraphs |
| `.pdf` | Text extracted page by page |
| `.zip` | Table of contents: filename, size, modified date |
| anything else | Error message: *"Cannot read …"* |

**Example:**

```bash
python email_tool.py attachment 12345 report.pdf
python email_tool.py attachment 12345 archive.zip
```

---

### `send` — Send an email

```bash
python email_tool.py send --to <addr> --subject <subject> \
    (--body <text> | --body-file <file>) \
    [--attach file1 file2 ...] [--compress]
```

| Option | Required | Description |
|---|---|---|
| `--to` | ✅ | Recipient email address |
| `--subject` | ✅ | Email subject |
| `--body` | one of | Inline body text |
| `--body-file` | one of | Path to a text file containing the body |
| `--attach` | ❌ | One or more files to attach |
| `--compress` | ❌ | Zip all attachments into `attachments.zip` before sending |

**Examples:**

```bash
# Simple message
python email_tool.py send \
  --to colleague@example.com \
  --subject "Quick note" \
  --body "Please see the attached files."

# Body from a file, multiple attachments, uncompressed
python email_tool.py send \
  --to colleague@example.com \
  --subject "Monthly report" \
  --body-file message.txt \
  --attach report.pdf data.csv

# Body inline, attachments compressed into a zip
python email_tool.py send \
  --to colleague@example.com \
  --subject "Project files" \
  --body "Files are compressed in the zip." \
  --attach design.docx notes.md screenshot.png \
  --compress
```

---

## Project structure

```
mail_utils/
├── email_tool.py     # Main script
├── requirements.txt  # Python dependencies
├── .env              # Credentials (not committed)
├── .env.example      # Credentials template
└── .venv/            # Virtual environment (not committed)
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Error: .env not found` | Copy `.env.example` to `.env` and fill in credentials |
| `IMAP LOGIN failed` | Check address and App Password; make sure IMAP is enabled in Gmail |
| `[SSL: CERTIFICATE_VERIFY_FAILED]` on macOS | Run `/Applications/Python 3.12/Install Certificates.command` |
| Subject shows garbled text | Upgrade to latest version — encoding fix is included |
| Attachment not found | Run `email_tool.py read <uid>` first to see exact filenames |
