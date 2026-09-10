# Running your league through GitHub

The goal: nobody in your league downloads Python, unzips anything, or follows a
setup process. Here is exactly how far that goes, and where the one hard limit
sits.

---

## What can and cannot move to GitHub

**Can, completely:**

| Thing | Where it lives | Who has to install anything |
|---|---|---|
| Driver sign-ups | A GitHub issue form | Nobody |
| The roster | `roster.json`, updated by a workflow | Nobody |
| Setup handbook, showroom preview, entry list | GitHub Pages | Nobody |
| Building the Windows app | GitHub Actions | Nobody |

**Cannot, ever:**

Reading iRacing's live telemetry. The sim publishes it into Windows shared
memory **on the computer running the sim**. A GitHub Actions runner is a cloud
VM in a datacentre with no connection to anyone's PC, and GitHub Pages is
static file hosting with no server process at all. Neither can see your
iRacing, and no amount of configuration changes that.

So something must run on a Windows PC that is in the session. The question is
only how painful that is, and how many people have to do it.

**The answer to both: one file, one person.**

---

## What each person actually does

| Role | What they do | Install |
|---|---|---|
| Driver | Fill in a web form once | Nothing |
| Steward, engineer, spotter | Open a URL | Nothing |
| Co-commentator on the same network | Open a URL | Nothing |
| Whoever runs the broadcast | Double-click `PitWall.exe` | One file |
| A driver who wants the in-game HUD | Double-click `PitWall.exe` on their own PC | One file |

That is the whole picture. One person per session runs one file, and everyone
else opens web pages that file serves.

---

## Setting it up

### 1. Put the repository on GitHub

Push this folder to a new repo. Public or private both work, though a public
one makes the Pages site and the sign-up form reachable without every driver
needing to be added as a collaborator.

### 2. Turn on Pages

Repository **Settings → Pages → Source: GitHub Actions**. Push to `main` and
the site publishes itself at:

```
https://<your-username>.github.io/<repo-name>/
```

That page is the link you send to your league. It carries the sign-up form, the
app download, the handbook, the showroom, and the live entry list.

### 3. Turn on workflow write access

**Settings → Actions → General → Workflow permissions →
"Read and write permissions"**. The sign-up workflow needs this to commit the
roster and reply on issues.

### 4. Cut a release so there is something to download

```
git tag v1.0.0
git push --tags
```

GitHub builds `PitWall.exe` on a Windows runner, **starts it and checks it
actually serves pages**, then attaches it to the release. A build that does not
run never reaches your league.

You can also trigger it by hand from the **Actions** tab without tagging.

### 5. Send one link

Send your drivers the Pages URL. That is the entire onboarding.

---

## How sign-ups work

A driver clicks **Sign up** and gets a GitHub issue form: customer ID, name,
number, team, country, a photo they can drag straight in, a one-line bio,
socials, accent colour.

When they submit:

1. A workflow reads the issue.
2. It validates the customer ID and name, and rejects the submission with a
   readable explanation if either is wrong. The most common mistake by a mile
   is entering a display name where the customer ID goes, so that gets its own
   error message pointing at **Account → My Account → Customer ID**.
3. It merges the driver into `roster.json` and commits it.
4. It replies on the issue with a table of exactly what it recorded, then
   closes it.

To change an entry, the driver **edits their own issue**. The workflow reruns
and the roster follows. No admin involvement, no new submission, no duplicate
entries — matching is on customer ID, so a re-submission updates rather than
appends.

Photos dragged into the form are hosted by GitHub, so the URL in the roster
works from any machine. That solves the one awkward part of the local sign-up
form, where uploaded images only existed on the PC that received them.

**Drivers need a free GitHub account** for this route. If that is a problem for
your league, keep using the app's own `/register` page during race week and
push the roster afterwards; both write the same file.

### Pulling the roster into the app

In `config/pitwall.json`:

```json
{
  "roster": {
    "mode": "url",
    "url": "https://raw.githubusercontent.com/<you>/<repo>/main/roster.json",
    "autoPullSeconds": 300
  }
}
```

Now every copy of the app pulls the same roster, refreshed every five minutes,
and whoever runs the broadcast that week gets everyone's entries automatically.

---

## Hosting the overlays on Pages

You can serve the overlay files from Pages and point them back at the machine
running the app:

```
https://<you>.github.io/<repo>/broadcast/tower.html?server=http://192.168.1.20:8099
```

**But you usually should not, and here is the honest reason.** GitHub Pages is
HTTPS. Browsers restrict what an HTTPS page may connect to, and a plain
WebSocket back to another machine on your LAN is exactly the kind of connection
they restrict. It works to `localhost` in some browsers and fails in others,
and the failure is silent.

The app already serves every overlay itself over plain HTTP on your own
network, where none of that applies. So:

- **Overlays and the timing page:** serve them from the app. Same machine or
  same LAN, no HTTPS, no restrictions.
- **Pages:** the handbook, the showroom, the sign-up form, the entry list. All
  static, no live data, no problem.

That split is why the Pages workflow publishes the documentation and the
sign-up hub, and not the overlays.

---

## Two things people try that do not work

**"Can a GitHub Action read my telemetry on a schedule?"** No. The runner is a
fresh VM in a datacentre. It has never heard of your PC.

**"Can the app push timing data to the repo and have viewers poll it?"**
Technically yes, and it is a bad idea. You would be committing a file several
times a second, blowing through API rate limits within minutes, with 5 to 30
seconds of latency on a display whose entire job is to be live. A timing tower
that is 20 seconds behind is worse than no timing tower.

**If you genuinely need a remote viewer** — a co-commentator in another city,
stewards watching from home — the working answer is a tunnel from the machine
running the app: Cloudflare Tunnel or Tailscale, both free, both a one-line
setup, both giving you a URL that reaches the app from anywhere. That is
outside GitHub, and it is the only thing in this document that is.

---

## What is in the repository

```
.github/workflows/release.yml     builds and tests PitWall.exe, attaches it to releases
.github/workflows/roster.yml      turns sign-up issues into roster.json
.github/workflows/pages.yml       publishes the site
.github/ISSUE_TEMPLATE/
  driver-signup.yml               the sign-up form drivers see
scripts/build_exe.py              the PyInstaller build
scripts/roster_from_issue.py      the issue parser and roster merge
site/index.html                   the landing page drivers get sent to
roster.json                       the league roster, maintained by the workflow
```

## Keeping the app up to date

Tag a new version and everyone downloads the new `.exe` from the same releases
link. Their `data` folder and `config` sit **next to** the executable, so
dropping in a newer build keeps the roster, the cached track maps and the
league branding intact.
