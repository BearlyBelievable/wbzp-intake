# wbzp-intake: web intake forms for Zulip, email, and beyond

[![License](https://img.shields.io/badge/license-PolyForm%20Internal%20Use%201.0.0-orange)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Flask](https://img.shields.io/badge/flask-3.1-black)
[![Version](https://img.shields.io/badge/version-2.0.0-informational)](CHANGELOG.md)
![Last commit](https://img.shields.io/github/last-commit/BearlyBelievable/wbzp-intake)
[![Tests](https://github.com/BearlyBelievable/wbzp-intake/actions/workflows/tests.yml/badge.svg)](https://github.com/BearlyBelievable/wbzp-intake/actions/workflows/tests.yml)

wbzp-intake aims to simplify the process of building an intake form
that submits from your website to a specific destination. No
third-party form service, no handing over private or sensitive data.

You define your form fields and destinations through simple YAML
files, and the app handles the rest: generating the form, validating
the fields, rate limiting submissions, and optionally protecting from
spam and bots with Cloudflare Turnstile protection. Your form
submissions can route to a [Zulip](https://zulip.com/) channel, an
email address, or both, and they can even branch to different paths
based on how a specific field was answered.

Built with [Pelican](https://getpelican.com/) in mind, but should
work with any static site (Zulip itself is optional).

## Requirements

- A systemd-based Linux server (Ubuntu or Debian) with Python 3.12 and
  its `venv` module (the `python3-venv` package).
- At least one usable destination for your form submissions:
    - **Email:** You need an SMTP service to deliver emails, and a way
      to find out about bounces. That's either an IMAP mailbox that
      receives them or an email provider whose dashboard you'll watch.
    - **Zulip:** You need a Zulip organization that the app can reach
      over HTTPS, and a bot user in it.
    - **Webhook:** (Planned but not yet implemented)
- (Optional) A Pelican checkout on the same server, if you want the
  form files generated in it.
- (Optional) A Cloudflare account and Turnstile key for spam
  protection.

## Installing and uninstalling

### First-time setup

1. Clone this repo to the server your site is served from (and your
   Zulip instance too, if you're using Zulip as a destination).
2. Run `sudo ./install.sh` and fill in the
   [configuration options](#configuration-options). Along the way it
   asks where submissions should go and runs the matching
   [destination](#destinations) setup for you.
3. Customize your [form templates](#building-your-forms) to fit what
   you need.
4. Run `sudo ./generate-form.sh` to generate the form HTML from
   them.
5. [Hook up your site](#hooking-up-your-site) to serve the generated
   forms.
6. Start the service and its timers with the command the installer
   printed. It's
   `sudo systemctl start wbzp-intake wbzp-intake-check-pending.timer`,
   plus `wbzp-intake-check-bounces.timer` if you set up
   [bounce checking](#bounce-checking).
7. Once everything's wired up, check it's live:
   `curl -i http://127.0.0.1:8793/intake/<template-name>` should
   return a non-502 response.

### Changing settings

If you need to update any settings, run `sudo ./install.sh` again to
go through the full setup again. Your answers are saved in
`instance/config.conf`, and your secrets (API keys and passwords) in
`instance/secrets.conf`, so you can also edit those files directly.
To change your destinations, run `sudo ./configure-email.sh` or
`sudo ./configure-zulip.sh` again. Restart the service afterward
(`sudo systemctl restart wbzp-intake`) to apply the change.

### Uninstalling

Run `sudo ./uninstall.sh`. It stops and removes the services, removes
the generated form files from your site, and offers to clean up the
reverse proxy wiring, `.venv`, local data, and any leftovers from
older versions of this app.

## Configuration options

### Site setup

Asks whether your site is Pelican or something else. For Pelican, it
asks whether to generate the form files in your checkout. If you
answer yes, it asks where the checkout is and adds
`STATIC_PATHS = ["extra"]` and `THEME_TEMPLATES_OVERRIDES = ["templates"]`
to `pelicanconf.py` if it doesn't already set them. Otherwise the files
are written to the `output/` folder in this app, and you can deploy
them to your site yourself.

### Reverse proxy (optional)

Detects nginx, Apache, or Caddy and offers to wire up the `/intake`
route for you. It keeps a backup of your site config in
`instance/backups/site-config/` before changing it. If you'd rather
set it up by hand, skip this and use the examples in
`deploy/reverse-proxy/`.

### Contact address

Prompts for the email address people should contact about their
submission. It's shown to anyone whose submission is still pending,
and it receives alert emails if you turn them on.

### Cloudflare Turnstile (optional)

Asks whether to set up Turnstile, then prompts for your site key and
secret. The site key is baked directly into each generated form's
Turnstile widget, so there's nothing else to configure manually.
Answering no leaves Turnstile off.

Turnstile is recommended, since it protects against distributed or
automated abuse that rate limiting alone doesn't catch. Without it,
`/intake/<name>` still enforces two independent rate limits, one on
the submitting IP address and one on the submitted email address, so
a single source can't spam submissions.

### SMTP (optional)

Alert emails and email destinations both send through an SMTP
service. The installer asks how the app will send email, groups the
choices by how you set them up, and fills in each provider's server
settings for you. The app refuses to start if alert emails are turned
on, or a destination sends email, without a working set of SMTP
settings.

**An email account:** Google, Yahoo, Fastmail, or Purelymail. You
enter your full address and an app password (a separate password made
for apps, which Google and Yahoo require and Fastmail needs too, and
Purelymail needs if you use two-factor authentication). Bounces
arrive in the account's inbox, so the installer can check them for you
over IMAP with the same login.

**A transactional email service:** Mailgun (US or EU), SendGrid,
Amazon SES, Postmark, or Resend. Create an SMTP login or API key for
this app in the provider's dashboard and enter it. SendGrid and Resend
use a fixed username, so you only enter the API key. SES also asks for
your AWS region. Most of these need a verified `From` address, which
the installer asks for. Bounces show up in the provider's dashboard
and webhooks, so the app doesn't check for them.

**Another service:** enter the server, port, login, password, and
`From` address yourself.

If Zulip's transactional email is already set up on the same server,
the installer also offers to use the same server. It copies Zulip's
host, port, and TLS setting, and you still enter a login and password
made for this app.

Connections use STARTTLS by default, or implicit TLS on port 465. If
a server needs something else, set `smtp_security` to `ssl`,
`starttls`, or `none` in `instance/config.conf`. If your login isn't
an email address, set `smtp_from` to the address emails should come
from.

Once email is set up, the installer offers to send a test email to
your contact address, and to log in to the bounce mailbox if you use
one, so a wrong password shows up right away. You can run the same
check later with `.venv/bin/flask --app app test-email`.

If you skip this, alert emails stay off and Email isn't offered as a
destination.

### Alert emails (optional)

If SMTP is set up, asks whether to send alert emails for technical
failures, failed deliveries, and bounces.

## Destinations

The installer asks where submissions should go (Email, Zulip, or
both, and if both, which one is the default) and runs the matching
setup scripts below. Email is only offered
if [SMTP](#smtp-optional) is set up. Pick "I'll set this up manually"
to skip this and run the scripts yourself later, or to edit
`instance/destinations.yaml` by hand. It has comments documenting the
format for both destination types.

Destinations are the places a form's [routing rules](#routing-rules)
can send submissions. The installer creates one named `default`, and
you can add any number of others. Each one is either an email address
or a Zulip channel. Each submission arrives as the form's questions
and the submitter's answers. Questions left blank show as
`(not answered)` in email and `_(user did not specify)_` in Zulip,
which also starts the post with the form's name.

### Email

Run `sudo ./configure-email.sh`, once [SMTP](#smtp-optional) is set up.
It asks about [bounce checking](#bounce-checking), and then prompts
for an email address and an optional subject (blank uses
`New wbzp-intake submission`) for each destination. The first one is
named `default`, and it asks for a name for any you add after it. Add any BCC addresses after the first address, separated
by commas. The app refuses to start with an email destination unless
SMTP and bounce checking are set up.

Each submission goes to the first address, and any others are BCC. The
`Reply-To` is the submitter's address, and the form's name is added to
the subject so you can tell forms apart.

```yaml
office:
    type: email
    address:
        - office@example.com
        - audit@example.com
    subject: Office request
```

#### Bounce checking

A submission sent by email can bounce after your SMTP service accepts
it, so the app needs a way to find out. What `configure-email.sh` asks
depends on the [SMTP](#smtp-optional) choice you made. Transactional
email services report bounces in a dashboard, so it skips them.

**An email account:** It offers to check the same account's inbox over
IMAP with the same login.

**Another service:** It asks whether to check an IMAP mailbox, then
prompts for its server, port, login, and password. The port defaults
to `993` (IMAP over SSL), and a blank login or password reuses your
SMTP one.

When checking is on, the `wbzp-intake-check-bounces` timer runs hourly
and reads unread messages in the mailbox's inbox. If bounces arrive
somewhere else, such as a folder a mail filter sorts them into, set
`bounce_imap_folder` in `instance/config.conf` or answer the folder
prompt. The timer logs each bounced address to the journal, and also
sends an alert email to your contact address if alert emails are on.
Bounce messages it handles are marked as read.

The timer is only enabled when checking is on and a destination sends
email. The choice is saved as `bounce_source` (`imap`
or `external`) in `instance/config.conf`. When it's `external`, the
app logs a warning each time the service starts.

### Zulip

Run `sudo ./configure-zulip.sh`. The first time you add a Zulip
destination, it prompts for your Zulip bot's email and API key, then
for your organization's URL (it suggests one based on the bot's email
domain). It confirms that the URL is a Zulip organization and checks
that the bot can see members' real email addresses (see
[Troubleshooting](#troubleshooting) if that check fails). Once those
are confirmed, you can select a channel to use. Options for channels
come from the channels the bot is already subscribed to, so subscribe
it to the desired channel in Zulip first.

Each Zulip destination posts to one channel, and they all share the
same bot. To send a form's submissions to another channel, run the
script again, or answer yes when it offers to add another. The first
destination is named `default`, and you give any others a name for a
routing rule to use. Each one also
needs a `topic`, which Zulip requires for every post. The script asks
for it and uses `New wbzp-intake submission`, the same default as
email subjects, if you leave it blank, or you can enter `general chat`
if your organization allows it.

```yaml
default:
    type: zulip
    channel_id: 12
    topic: New wbzp-intake submission
youth:
    type: zulip
    channel_id: 15
    topic: Youth applications
```

Before posting, the app checks Zulip for the submitter's email,
whichever channel a rule picked. An email that already has an account
is turned away, and one that was already submitted and is waiting on
review gets the pending message. Email destinations
skip this check, so a submitter can submit again as soon as the email
is sent, within the rate limits. See
[Detecting duplicate Zulip submissions](#detecting-duplicate-zulip-submissions).

The bot needs to see members' real email addresses, or registered
users won't be recognized. That works when your organization's "Who
can access user email addresses" setting is Everyone or Members, or
when the bot is an administrator. Zulip doesn't let bots read
invitations, so by default a pending invite isn't detected. If you
run the app on the Zulip server, see
[Checking Zulip's database directly](#checking-zulips-database-directly)
for a way to detect them.

## Building your forms

Each file in `forms/` defines one form in YAML, and
[`forms/application.yaml`](forms/application.yaml) is a full working
example. After you add or edit one, run `sudo ./generate-form.sh` to
regenerate the embeddable HTML. The service notices the change on
its own, but a form that uses a new destination needs a restart after
you add the destination.

Every form needs a set of `routing_rules`, where the last rule only
needs a `destination`, and a set of `fields`, one of which needs to be
an `email` field named `submitter_email`. More details are below:

### Routing rules

`routing_rules` is a list of rules, and the first rule that matches
decides which [destination](#destinations) a submission goes to. The
last rule has only a `destination` and catches everything else, so a
simple form only needs:

```yaml
routing_rules:
  - destination: default
```

To send some submissions elsewhere, put rules above the last one that
check an answer. Each of those rules needs a `trigger_field_name` and a
`condition` together. [`forms/application.yaml`](forms/application.yaml)
has an example to uncomment.

| Key | Value | Meaning |
|---|---|---|
| `destination` | `string` | Name of the destination to use if this rule matches. |
| `trigger_field_name` | `string` | Name of the field whose answer is checked. |
| `condition` | `string` or `list` | What the answer has to be. |

**Number fields:** The condition is a comparison in quotes, such as
`"<18"` or `">=12"`, using `<`, `<=`, `>`, `>=`, `=`, or `!=`. Keep the
quotes, because YAML reads an unquoted `>` at the start of a value as
something else.

**Other fields:** The condition is a list of answers, and the rule
matches when the answer is any of them. A `multiselect` matches when
any selected option is listed. For `select` and `multiselect` fields,
each answer must be one of the options, and for a `boolean` field, use
`[true]` or `[false]`.

### Form fields

Every field requires these keys to be valid:

| Key | Value | Meaning |
|---|---|---|
| `name` | `string` | Form field name. Must be unique across the whole file. |
| `type` | `number`, `text`, `email`, `textarea`, `select`, `multiselect`, or `boolean` | The kind of field. |
| `label` | `string` | The question shown to the submitter. |
| `required` | `boolean` | Whether the field must be filled in. |
| `note` | `dict` | A callout shown under the field. |

A `note` can be left as an empty dict (`{}`) if you don't want one
shown. To show a callout, fill it out with all three of:

| Key | Value | Meaning |
|---|---|---|
| `type` | `info`, `caution`, or `warning` | The callout's style. |
| `title` | `string` | The callout's heading. |
| `text` | `string` | The callout's body. |

#### `number` fields can have a `range` field

| Key | Value | Meaning |
|---|---|---|
| `range` | `dict` | Value bounds. |

`range` takes `min` and/or `max`. There's no default for either.

#### `text`, `email`, and `textarea` fields can have a `length` field

| Key | Value | Meaning |
|---|---|---|
| `length` | `dict` | Character-count bounds. |

`length` takes `min` and/or `max`. Default `max` comes from
`max_text_length` (`text`/`email`) or `max_textarea_length`
(`textarea`) in `instance/config.conf` when omitted. There's no default
`min`.

#### `select` and `multiselect` fields must have an `options` field

| Key | Value | Meaning |
|---|---|---|
| `options` | `dict` | The selectable choices. |

Each option in `options` is structured as a key/value pair to
support extended functionality. The form will use the key as the
choice's exact text, and if you
want just a plain option, you can leave the choice's value as an
empty dict (`{}`). If you want to show a follow-up field when a
specific choice is picked (like gathering more details when "Other"
is selected), then fill out that value dict as a full sub-field. If
you use a select or multi-select field again, it can reveal
follow-up fields of its own the same way, but only up to 2 levels
deep. See the `role_interest` field in
[`forms/application.yaml`](forms/application.yaml) for a
working example.

#### `boolean` fields can have an `options` field

| Key | Value | Meaning |
|---|---|---|
| `options` | `dict` | Follow-up fields for `true` and/or `false`. |

Works the same as `select`/`multiselect`, except the only keys
`options` can use are `"true"` and `"false"`, since those are the
only two values this field can take (shown to the submitter as Yes
and No). Leave `options` out entirely for a plain Yes/No field with
no follow-up.

## Hooking up your site

Each template generates its own `<name>-form.html`, with the
`<form>` element, every defined field, the message element, the
submit button (disabled until every required field validates), and
the `<link>`/`<script>` tags for its CSS and JS. It should be
essentially ready to drop into any site as-is.

**For Pelican sites:** every `<name>-form.html` and the starter page
template
([`generator/page-template.example.jinja`](generator/page-template.example.jinja))
are written into your `THEME_TEMPLATES_OVERRIDES` directory. The
template extends your theme's `base.html` and is yours to customize
(it won't be overwritten again). From there, just add
`Template: <name>` to the meta-data of whichever content page you
want that form to appear on. Rebuild your site any time
`install.sh` or `generate-form.sh` updates these files.

**Any other site:** every `<name>-form.html`, plus
`wbzp-intake-form.js` and `wbzp-intake-form.css`, are written to the
`output/` folder in this app. Copy them to your site, and each
generated HTML file's contents can go wherever you want that form to
appear on your page.

### Styling the components

The form uses CSS classes that you can style directly, like
`.wbzp-intake-form`, `.wbzp-intake-field`,
`.wbzp-intake-radio-group`, `.wbzp-intake-checkbox-group`, and
`.wbzp-intake-message` (with `is-success` or `is-error` once a
submission finishes). The form highlights an invalid field with a
`.has-error`, and shows a live `used / max` count in a
`.wbzp-intake-char-counter` element once a text field is close to
its length limit. Both use normalized default colors that you can
replace by setting the `--wbzp-intake-error-color` and
`--wbzp-intake-warn-color` CSS custom properties in your
stylesheet.

## Troubleshooting

### Zulip bot check fails during setup

`configure-zulip.sh` checks the bot and the URL as soon as you enter
the URL, and asks for the bot's email, API key, and URL again if the
check fails. It confirms that the address is a single Zulip organization
and that the bot can see members' real email addresses. If it reports
that the address doesn't look like a Zulip server, or isn't one
organization on a server, enter the organization's URL. If it reports
that the bot can't see email addresses, set "Who can access user email
addresses" to Everyone or Members in Zulip, or give the bot the
Administrator role, and enter the URL again. An error from the Zulip
API, such as HTTP 401, usually means the bot's email or API key is
wrong.

### Handling technical failures

If a submission fails to deliver, it's saved locally in
`instance/submissions.db` instead of being lost, and the submitter is
shown a submission-failed message on the page. That email address
can't submit again until the saved submission is delivered. The
`wbzp-intake`, `wbzp-intake-check-pending`, and
`wbzp-intake-check-bounces` units log to the systemd journal
(`journalctl -u <unit name>`) with what went wrong.

If alert emails are enabled, your contact address also gets an
automated email about it. It's the only proactive notice you'll
get, since otherwise you'd only find out by checking the logs or
the database yourself.

Submissions that failed to deliver are retried automatically by the
same timer that rechecks submissions awaiting signup. You can run
`flask --app app check-pending` manually to retry sooner once
whatever caused the failure is fixed.

### Database upgrades

The service upgrades the database each time it starts, so restarting
it after an update is all you need. If an upgrade fails, that step is
rolled back, the service doesn't start, and the reason is in the
journal. Running other commands against a database that hasn't been
upgraded gives an error telling you to run `flask db-migrate`.

Before any upgrade, the app copies `instance/submissions.db` to
`instance/backups/` and keeps the newest five copies. If the backup
can't be written, the upgrade doesn't run. To go back to a backup,
stop the service and replace `instance/submissions.db` with it,
removing any `submissions.db-wal` and `submissions.db-shm` files next
to it.

### Detecting duplicate Zulip submissions

This only applies to submissions routed to a Zulip destination.

Before posting a submission, the app asks Zulip whether the
submitter's email already has an account (a deactivated account
doesn't count). The result is one of:

- **Already has a Zulip account:** The submitter sees a simple
  "That email address can't be used" message.
- **A submission was sent but no account yet:** The submission
  is kept as a local record in `instance/submissions.db` until it expires
  (per `submission_expiry_days`). The submitter is told it's still
  pending and given the configured contact address for anything
  urgent.

A submission is only posted to Zulip and recorded as awaiting signup
once it clears both checks. A submission to a non-Zulip
destination skips this check entirely and isn't kept once delivered.

The `wbzp-intake-check-pending` timer runs
`flask --app app check-pending` daily, so a Zulip submission awaiting
signup clears without any action from you.

## Customizing messages

The messages submitters see, such as rate limiting, verification, and
duplicate-submission notices, are in `strings.yaml`. Edit the text and
restart the service. Keep the `{contact_email}` placeholder in the
messages that have it, and don't add others. The app refuses to start
if a message is missing or its placeholders don't match.

## Commands

Run these from this folder with `.venv/bin/flask --app app <command>`.

| Command | What it does |
|---|---|
| `test-email` | Sends a test email to your contact address and checks the bounce mailbox login. |
| `check-pending` | Rechecks submissions awaiting signup and retries failed ones. |
| `bounce-check` | Reads the bounce mailbox once. |
| `clear-rate-limit` | Clears the rate limit records, or only those for one `--ip` or `--email`. |
| `db-migrate` | Upgrades the database, or rolls it back with `--to <version>`. |

## Advanced settings

These settings already have sensible defaults, but you can edit the
value in `instance/config.conf` if you want something different.

| Key | Default | Meaning |
|---|---|---|
| `max_attempts_per_ip` | `10` | Submissions allowed per rolling hour from one IP address. |
| `max_attempts_per_email` | `3` | Submissions allowed per rolling hour for one email address. |
| `rate_limit_window_minutes` | `60` | Length of the rolling window used for both limits above. |
| `submission_expiry_days` | `30` | How long a submission awaiting signup is kept before a resubmission is treated as new. |
| `zulip_lookup` | `api` | How the app checks whether a submitter's email already has a Zulip account. `manage_py` also detects pending invites, see [Checking Zulip's database directly](#checking-zulips-database-directly). |
| `smtp_security` | `starttls` | How to secure the SMTP connection: `starttls`, `ssl`, or `none`. |
| `bounce_imap_port` | `993` | IMAP port for the [bounce mailbox](#bounce-checking). |
| `bounce_imap_folder` | `INBOX` | IMAP folder the bounce check reads. |
| `zulip_manage_py` | `/home/zulip/deployments/current/manage.py` | Location of Zulip's `manage.py`, used when `zulip_lookup` is `manage_py`. |
| `max_body_bytes` | `8192` | Largest `/intake/<name>` request body accepted. Raise this if a large set of fields makes a legitimate submission exceed it. |
| `max_text_length` | `250` | Default character limit for a `text`/`email` field with no `length.max` set. |
| `max_textarea_length` | `1000` | Default character limit for a `textarea` field with no `length.max` set. |

### Checking Zulip's database directly

Zulip doesn't let bots read invitations, so by default the app can
only tell whether an email already has an account. If the app runs on
the same server as Zulip, you can set `zulip_lookup` to `manage_py` in
`instance/config.conf` and it asks Zulip's database directly through
`manage.py shell` instead. That also detects pending invites, so a
submitter with one is told to check their inbox, and it no longer
depends on the bot's view of email addresses.

The service has to run as a user that can run Zulip's `manage.py`,
normally the `zulip` user.
