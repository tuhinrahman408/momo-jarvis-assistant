# Momo - Personal AI Assistant

Speak or type a command in **English, Bengali or Banglish**. Momo answers in the language you used.
Everything the program itself prints is English by default.

```
run.py -> window (keyboard / voice) -> parser.py (Gemini + system prompt) -> schema.py (validates the JSON)
       -> orchestrator.py -> executor/handlers.py (browser, calls) or skills/ (PC, files, office, code, security)
```

## Setup
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then put your GEMINI_API_KEY (and GEMINI_MODEL) in .env
```
If PyAudio fails on Windows: `pip install pipwin && pipwin install pyaudio`.

## Run
```bash
python run.py                 # opens the Momo window
python run.py --login         # once: log in to WhatsApp, Facebook, Instagram ... in the Momo Edge window
python run.py --terminal      # coloured text mode (prompt: "Your command >")
python run.py --voice         # text mode, but with the microphone
python run.py --dry "call Rahim on WhatsApp and say I will be late"   # show the JSON only
python -m pytest
```

## The window
- Left side, three buttons: **keyboard**, **voice**, **close**.
- At start Momo says **"Sir, your command."** (keyboard mode is selected).
- Click **voice**: Momo says **"Sir, I am listening to you."** once, then listens until you switch back.
- Click **keyboard**: Momo says **"Sir, your command."** once. Selecting a mode again does nothing.
- The orb shows what Momo is doing: blue = ready, green = listening, purple = thinking, orange = working, pink = speaking.
- Closing the window stops the program, but a task that is already running (a message, a call ...)
  is finished first. Edge, its tabs and opened apps are never closed by Momo. Pending reminders stop with the program.

## What Momo says (and shows)
- Every reply is shown and spoken.
- A task (message, call, todo, file ...): **"Sir, <task> working right now."** and then the result.
- If it cannot finish: **"Sir, I could not complete your work. Reason: <short reason>."**
- Questions and chat: just the answer.
- These fixed lines are always English; change them at the top of `momo/orchestrator.py`.
- Full error details go to `~/Momo/logs/momo_debug.log`; a failed web task also saves `~/Momo/logs/last_failure.png`.

## Tabs
Momo uses Edge's empty start tab first, then reuses the tab of the same site (YouTube always in the same YouTube tab,
WhatsApp in the WhatsApp tab ...), even a tab that was opened in an earlier run. A new tab opens only for a new site.

## Chrome with a profile
"open chrome with asif profile", "chrome e tuhin profile diye google open koro", "list my chrome profiles".
Profiles are read from Chrome's own profile list; you can use the profile name, the Google account name or the email.

## Voice replies
Momo speaks every reply. Engines are tried in turn: Microsoft neural voice (online, English and Bengali), the Windows
voice (offline), Google voice. If none works, the window shows "Voice output problem: ..." with the reason.
Check which engine works on your PC: `python run.py --test-voice`. Voices can be changed with `VOICE_EN`, `VOICE_BN`
and `VOICE_ENGINE` in `.env`.

## Calling and messaging
Say the platform and the person: "Facebook e Rakib ke message dao ...", "call Rahim on WhatsApp and tell him ...".
Supported: WhatsApp, Facebook (Messenger), Instagram (messages), Telegram, LinkedIn, Gmail.
For the most reliable chats put the person in `contacts.json` (number / username = no search, no guessing):
`{"Rakib": {"email": "...", "whatsapp": "+8801XXXXXXXXX", "telegram": "@user", "facebook": "profile.name", "aliases": ["rakib vai"]}}`
(the old `"Rakib": "email"` form still works). `contacts.json` is private: it is in `.gitignore`.

Before anything is sent or called, the window shows a card (app, person, message) with **Yes / No** buttons.
Momo chooses a chat by the person's **name**, never just the first result. If no chat has that name nothing is sent;
if several chats match, Momo asks which one. Every command is written to `~/Momo/logs/momo_debug.log` as
"UNDERSTOOD: ..." so you can see how Momo understood it.

Calls: Facebook Messenger calls work in the browser. WhatsApp Web usually has no call button, so Momo then uses
**WhatsApp Desktop** (Windows, `pip install pywinauto`, the number in `contacts.json`). Telegram web cannot call.

On a call Momo first says salam, then introduces itself, then says your words exactly:
"Assalamu alaikum. I am the assistant made by Tuhin vai. Tuhin vai asked me to let you know: <your words>"
(change the name with `OWNER_NAME` in `.env`).
To let the other person hear Momo install a virtual audio cable (VB-Cable), set `CALL_AUDIO_DEVICE=CABLE Input`
in `.env` and, in the Momo Edge window, choose **CABLE Output** under Settings > Cookies and site permissions > Microphone.

## Skills
| Area | Examples |
|---|---|
| Computer control | lock / shut down / restart PC, open or close an app, create / rename / delete (recycle bin) files, screenshot, volume, brightness, type text, press keys, click the mouse |
| Internet | Google search, open sites, YouTube search and play, read unread mail / chats / schedule |
| Programming | write / explain / debug code, create a project, build and open a website, run a Python file, git |
| Personal | to-do list, reminders (spoken when due), daily routine, daily report, calendar events |
| Documents | Word, Excel (with formulas), PowerPoint, PDF summary, csv/xlsx/json conversion, email drafts |
| Memory | "remember that ...", "what do you remember about ...", project notes, suggestions from your habits |
| Security (own PC/network) | PC status, suspicious process check, connections, devices, log analysis, report file |

Files go to `~/Momo` (set `MOMO_WORKSPACE` to change); file skills only work inside your home folder.
Risky actions (shutdown, restart, close app, delete, type text, run code, network scan, git commit/push) ask `y/n` first.
The network scan only scans the private /24 subnet your PC is on. Add your own apps for "open ..." in `apps.json`.

## Notes
- Website selectors change often; if one breaks, only edit the selector lists at the top of `executor/handlers.py`.
- Use automation only on your own accounts.
- Never share `.env`, `contacts.json`, `browser_profile/` or the `MomoEdge` folder (API key, numbers, logged-in sessions).
- If a website changes and a button is not found, the visible buttons are written to `~/Momo/logs/momo_debug.log`.
