# Changelog

## 2.1.0

- Added a lookup helper for Zulip account checks, for when the app runs
  on the Zulip server. It detects pending invites and works with an
  Incoming webhook bot. Select it with `zulip_lookup = helper`.
- The Zulip API lookup now needs a Generic bot with the Moderator role
  and the "Admins and moderators" email visibility setting.
- The install and configure scripts now offer to start and restart
  services.
- Zulip API errors now show the URL called and Zulip's reason.

## 2.0.0

- Restructured the app as a proper Flask application. `app.py` is now
  an application factory, the code lives in an `intake/` package, the
  config, secrets, and database files live in Flask's `instance/`
  folder, and the maintenance commands are Flask commands run with
  `flask --app app <command>`. Settings that used "application" are
  renamed to "submission".
- Replaced gunicorn with waitress and renamed the systemd units to
  `wbzp-intake*`. The install scripts no longer start services, so
  start them with `systemctl`.
- Added database migrations with automatic backups. Failed
  submissions are now saved and retried.
- Submissions can now go to an email address as well as a Zulip
  channel. Destinations live in `instance/destinations.yaml`, and
  `install.sh` runs the new `configure-email.sh` and
  `configure-zulip.sh` to set them up.
- Added optional alert emails for failures and bounces, SMTP setup from
  a list of providers, an hourly bounce mailbox check for email
  destinations, and a `test-email` command to check your settings.
- Zulip account checks now use the Zulip API instead of
  `manage.py shell`, so the app no longer has to run on the Zulip
  server and the bot only needs to see members' email addresses. Zulip
  doesn't let bots read invitations, so pending invites are detected
  only if you set `zulip_lookup` to `manage_py`.
- Replaced `application-fields.json` with one YAML file per form in
  `forms/`. Each form needs `routing_rules`, which pick a destination
  based on answers, and a required `submitter_email` field.
- Changed the submission URL from `/apply` to `/intake/<template-name>`.
- Renamed the form's CSS classes, IDs, and data attributes to start
  with `wbzp-intake-`.
- New and edited forms are picked up without a restart.

## 1.0.0

- Replaced `conditional_options` with nested field definitions directly
  in `options`, also supported on `boolean` fields via `true`/`false`
  keys, capped at 2 levels deep. `select` now requires at least 2
  options and `multiselect` at least 1.
- Collapsed `note_title`/`note_text`/`note_type` into `note`, `min`/`max`
  into `range`, and `minlength`/`maxlength` into `length`. `note` is now
  required on every field (`{}` for none).
- Fixed `note` only rendering on `text`/`email` fields.
- Split form generation out of `install.sh` into a standalone
  `generate-form.sh`.
- Zulip site URL and channel detection during install now use
  `manage.py shell`, scoped to what the bot can access.
- Added `uninstall.sh`, which stops the services, removes the generated
  site files, and offers to clean up reverse proxy wiring, `.venv`, and
  local data. `install.sh` also detects and cleans up leftovers from
  older versions when updating a deployment: a dead
  `data/application-limits.json`, the old `pelicanconf.py`
  `application_fields` wiring, and the old full-page template.
- Added a pytest test suite.
- Added `.gitattributes` so every file stays LF regardless of what
  platform edits it.

## 0.5.0

- Reorganized the app's source into `app/` and `generator/`, and
  `install.sh` now generates `application-form.html` directly from
  `application-fields.json`, replacing the separate Pelican and
  vanilla-JS implementations.
- Renamed `application-fields.template.json` to `application-fields.json`.
- Renamed the note field's `note` key to `note_text`. `note_title`,
  `note_text`, and `note_type` had to be set together or not at all.
- Added a `LICENSE` (PolyForm Internal Use 1.0.0).

## 0.4.0

- Extracted the application form's script and stylesheet into their
  own files instead of embedding them in the site theme.

## 0.3.0

- Text length limits now come from `config.conf` instead of being
  hardcoded.

## 0.2.1

- Fixed conditional companion fields not syncing and validating
  correctly before submitting.

## 0.2.0

- Submissions now post to a Zulip channel instead of being emailed
  directly.

## 0.1.1

- Fixed reverse-proxy config backups being stored in the wrong
  location.

## 0.1.0

- Initial release.
