# Keeping the roster in GitHub

The roster is the list of driver-submitted profiles: headshot, country, team,
pronouns, hometown, bio, sponsor, socials, accent colour, all keyed by iRacing
customer ID.

By default it lives in `data/roster.json` on the machine running PitWall. That
is fine for one person. For a league it is the wrong place, because:

- Only the broadcast PC has it. If someone else runs the stream one week, the
  driver cards are empty.
- There is no history. Someone edits the wrong entry and it is just gone.
- There is no way for a co-admin to fix a typo without remoting into your PC.

Putting `roster.json` in a GitHub repo fixes all three. The repo becomes the
league's database: submissions land as commits, you get history and rollback for
free, and any PitWall instance can pull it.

---

## Option 1 — read-only (simplest, no token)

Good when one person collects sign-ups and everyone else just needs the data.

1. Make a repo, e.g. `your-league/roster`. It can be public or private.
2. Put a `roster.json` in it. Export one from the control panel's **Roster**
   tab, or start with `{"drivers": []}`.
3. In `config/pitwall.json`:

```json
{
  "roster": {
    "mode": "url",
    "url": "https://raw.githubusercontent.com/your-league/roster/main/roster.json",
    "autoPullSeconds": 300
  }
}
```

Anyone running PitWall now pulls the same roster, refreshed every five minutes.
`autoPullSeconds: 0` turns off automatic refresh; there is always a **Pull**
button on the control panel.

For a private repo, use `mode: "github"` below — raw URLs on private repos need
a token anyway.

---

## Option 2 — read and write (submissions commit themselves)

This is the one to use if you want driver sign-ups to land in the repo.

### 1. Create the repo

`your-league/roster`, with a `roster.json` containing `{"drivers": []}`.

### 2. Create a token

GitHub → Settings → Developer settings → **Personal access tokens** → **Fine-grained
tokens** → Generate new token.

- **Repository access:** only `your-league/roster`
- **Permissions:** Repository permissions → **Contents: Read and write**
- Nothing else. This token can touch one file in one repo and nothing else in
  your account.

Set an expiry you will actually remember to renew. Ninety days is sensible.

### 3. Give the token to PitWall

Do **not** put it in `config/pitwall.json` if that file is going anywhere near a
repo. Use the environment variable instead:

**Windows, permanently:**
```
setx PITWALL_GITHUB_TOKEN github_pat_xxxxxxxxxxxx
```
Close and reopen your terminal, then start PitWall normally.

**Windows, just for this session:**
```
set PITWALL_GITHUB_TOKEN=github_pat_xxxxxxxxxxxx
run.bat
```

PitWall reads `PITWALL_GITHUB_TOKEN` and never displays it, never logs it, and
never sends it to a browser. The control panel only ever shows whether a token
is present.

### 4. Configure

```json
{
  "roster": {
    "mode": "github",
    "repo": "your-league/roster",
    "branch": "main",
    "path": "roster.json",
    "autoPullSeconds": 300
  }
}
```

### 5. Use it

On the control panel's **Roster** tab:

- **Pull** replaces the local roster with the repo's copy.
- **Push** commits the local roster back, as
  `PitWall roster update (N drivers)`.

A sensible race-week rhythm: Pull when you start, send drivers the sign-up link,
Push once sign-ups close.

---

## What about headshots?

Uploaded images are written to `data/uploads/` and referenced as
`/uploads/<file>`, served by PitWall itself. Those files are **not** pushed to
GitHub — only `roster.json` is.

If you want images shared across machines, either:

- **Host them yourself and paste links.** Any public image URL works in the
  headshot field: a GitHub repo (use the `raw.githubusercontent.com` link), an
  imgur link, your league's website. This is the simplest route and it works
  across every machine immediately.
- **Or copy `data/uploads/` alongside the roster.** Commit the folder to the same
  repo and set the headshot field to the raw URL.

Keep headshots small. The sign-up form already downscales uploads to 512px, but
a linked image is served at whatever size you point at, and OBS will happily
load a 4MB photo for a graphic that displays it at 120px.

---

## Also worth doing with the repo

Since you have a repo anyway:

- **Host the overlays on GitHub Pages.** Every page in `web/` is static. Push
  `web/` to a Pages site and your OBS browser sources can point at
  `https://your-league.github.io/overlays/broadcast/tower.html?server=http://192.168.1.20:8099`.
  The `?server=` parameter tells the page which machine to get data from. Useful
  when a co-commentator on another PC needs the same graphics.
- **Version your scene URLs.** Keep a text file of the exact browser-source URLs
  your show uses, so rebuilding a scene collection is copy and paste.
- **Track config changes.** `config/pitwall.json` in the repo means your league
  branding and column choices are reproducible.

## Security notes

- The token is scoped to one repo with one permission. If it leaks, the damage
  is limited to that file.
- PitWall's web server has no authentication and is meant for your own network.
  Do not port-forward it to the internet. If you need a remote co-commentator to
  reach it, use a tunnel you control (Tailscale, Cloudflare Tunnel) rather than
  opening a port.
- The sign-up form accepts submissions from anyone who can reach it. On a normal
  home network that means people in your house. If you share it more widely, be
  aware that customer ID is the only key, so someone could overwrite another
  driver's entry by guessing theirs. Pushing to GitHub gives you the history to
  undo that.
