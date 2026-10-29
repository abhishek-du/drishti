# Drishti systemd user units (the user installs them)

```bash
# 1. stop the hand-started copies (they hold ports 8700 / 3100)
/home/cis/windows/drishti/ops/runbooks/drishti_serve.sh stop
# 2. install and start
mkdir -p ~/.config/systemd/user
cp /home/cis/windows/drishti/ops/systemd/drishti-{api,web}.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now drishti-api.service drishti-web.service
# 3. keep them running without a login session (after a reboot too); Linger is "no" today
loginctl enable-linger cis
# check
systemctl --user status drishti-api drishti-web --no-pager
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/
```

- After a web change: `cd web && npx next build && systemctl --user restart drishti-web`.
- After a backend change: `systemctl --user restart drishti-api`.
- Once the units are installed, `drishti_serve.sh` should no longer be used for start.
