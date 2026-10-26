# Nginx Site Configuration

## Hosts and upstream

| Purpose | Host / origin | Destination |
|---|---|---|
| Local API Nginx host | `youju.942295.xyz` | `127.0.0.1:8089` |
| Client API origin | `https://youju-h.942295.xyz` | Existing public ingress, then local API host |
| Local H5 Nginx host | `youjuh5.942295.xyz` | `client/dist/h5` |
| Verified public H5 ingress | `https://youjuh5-h.942295.xyz` | Public ingress, then local H5 host |

`nginx.conf.d/youju.conf` follows the local Yizhu/Xiaoman site pattern and uses
the existing shared certificate paths under `/etc/nginx/ssl/942295/`. The API
client origin relies on the reference ingress removing `-h` before forwarding.
This repository does not configure external DNS or that ingress. Live verification
confirmed the existing `-h` ingress for API and H5. The requested local H5 server_name
remains unchanged; no second local H5 site was added. The public non-`-h` H5 URL
currently serves the external gateway's default Nginx page, not this application.
Changing that external route is still required if it must be the public entry URL.

The API forwards only `/api/`, preserving that prefix. Internal RecipeService RPC
and other paths are not published. H5 blocks `/api`, `/api/`, dotfiles, source maps,
private/source files and SQLite files. Missing JS/CSS/assets return 404 rather than
SPA HTML. The static root is **only**:

```text
/home/zhang/workspace/xdog-youju/client/dist/h5
```

Do not point Nginx at the repository, server storage or client source tree.
`index.html` and stable JS/CSS names revalidate after builds; media/font assets
have a short cache lifetime. The production H5 needs no Taro development listener.

## Application configuration

The ignored `client/.env.local` now sets the requested public API base. Existing
AppID/upload-key settings are retained, and `project.config.json` is aligned with
the locally supplied public AppID; WeChat URL checks stay enabled. Only public
`TARO_APP_*` values belong in the client. No auth client secret, upload private key,
or certificate private key is copied to build output or site configuration.

The ignored server `.env` retains the user's dedicated auth client credentials,
database/storage settings and administrator grants. The updated public settings are:

```sh
YOUJU_ALLOWED_ORIGINS='["https://youjuh5.942295.xyz","https://youjuh5-h.942295.xyz","http://127.0.0.1:10089","http://localhost:10089"]'
YOUJU_TRUSTED_HOSTS='["youju.942295.xyz","youju-h.942295.xyz","127.0.0.1","localhost"]'
```

Load server settings explicitly as documented in `ADMIN_V1.md`. The Nginx upstream
requires a separately started API process on loopback port 8089. A constant Python
service should use a production WSGI runner, not a published Flask debug server.
The approved deployment now runs `youju-api.service` using `youju.production` and
Waitress on loopback. No source/recipe worker was installed or started.
Database initialization/import and crawler/model execution remain separate operator
steps; configuring a domain does not initialize or activate source data.

## Build and review

```sh
npm --prefix client run typecheck
npm --prefix client test
npm --prefix client run build:h5
npm --prefix client run build:weapp
```

Confirm both compiled clients contain the requested API origin. Local H5 development
can explicitly set `TARO_APP_API_BASE_URL=` to use the existing localhost API proxy.
No preview/upload action is implied by building the mini-program.

## Installation and future changes

Installing into `/etc/nginx/conf.d` and reloading affects a shared service. These
site was installed and reloaded with explicit user approval on October 13, 2026.
Future shared-service changes still require approval; the installation steps are:

```sh
sudo install -m 0644 nginx.conf.d/youju.conf /etc/nginx/conf.d/youju.conf
sudo nginx -t
# Reload only after a successful test and explicit deployment approval.
sudo systemctl reload nginx
```

On this host SELinux is enforcing. The initial H5 output was labeled `user_home_t`, unlike the working reference
sites (`httpd_sys_content_t`). The approved deployment added the exact project-scoped
persistent rule below and relabeled the build tree to `httpd_sys_content_t`. A POSIX
read check alone does not establish that the actual `httpd_t` workers can serve it.
Before enabling the site, review a **project-scoped persistent** file-context rule
and relabel the built static tree. Do not disable SELinux or change unrelated sites:

```sh
sudo semanage fcontext -a -t httpd_sys_content_t \
  '/home/zhang/workspace/xdog-youju/client/dist/h5(/.*)?'
sudo restorecon -Rv /home/zhang/workspace/xdog-youju/client/dist/h5
```

If a rule already exists, inspect and update that exact rule rather than adding a
duplicate. Reapply `restorecon` after builds when necessary. Review ancestor search
permissions and SELinux denials during actual service verification. The existing
`httpd_can_network_connect` boolean is already enabled; it was not changed.

After approval/install and API startup, verify API health, allowed/denied CORS,
static HTML/assets, unknown assets, private-file rejection and authentication through
the actual ingress. Configure the HTTPS request domain in WeChat and verify on a real
device before claiming mini-program readiness. Do not disable URL checks to pass.

## Recorded preparation — October 13, 2026 (before approval)

- Site syntax passed `nginx -t` against an isolated configuration including only this
  site and the shared certificate paths. No live site was installed or reloaded.
- Shared certificate hostname checks matched both local hosts. The inspected local
  certificate expires on **October 30, 2026**; arrange renewal independently.
- The `nginx` account passed a POSIX static-file read check. SELinux service-context
  access still needs the scoped labeling/deployment step described above.
- Production H5-origin preflight, approved Host and rejected Host checks passed against
  the Flask factory with authentication deliberately unconfigured: no issuer calls.
- TypeScript and eight client tests passed; official H5/weapp builds succeeded and both
  output trees contained the public API base. H5 retained its entry-size warning.
- Port 8089 had no listener during preparation. Public ingress, live authentication,
  WeChat device login and upload have not been verified or performed.

The syntax-check artifacts are under `server/.artifacts/nginx-config-20261013/`.
No credentials are present in these reports. Root AGENTS.md and reference projects
were not modified.

## Approved live deployment — October 13, 2026

- Added locked project dependencies Waitress and python-dotenv; uv.lock now resolves
  77 packages. The production module loads only its working-directory `.env`, honors
  operator environment overrides and refuses missing DB/auth/trusted-host setup.
  It never creates a database, runs a debug server or starts an Agent/crawler.
- Initialized the configured **fresh, empty product database** at
  `server/youju.sqlite3`, mode 0600. No canonical POC database, diagnostic DB, source
  recipe or historical material was imported or activated. Admin subjects remain
  unchanged and currently number zero.
- Installed/enabled `systemd/youju-api.service` as `/etc/systemd/system/youju-api.service`.
  It runs as zhang with NoNewPrivileges, PrivateTmp, ProtectSystem and restrictive
  umask. The initial direct Python symlink entry was denied by SELinux at EXEC; the
  unit now uses the reference project's system Bash `exec` pattern. SELinux and
  NoNewPrivileges remain enabled; no broad home/interpreter relabeling was performed.
- The API is active on **127.0.0.1:8089 only**, with four Waitress threads. Forwarded
  client-IP/protocol headers are trusted only from the loopback proxy, one hop.
  Public TLS remains at Nginx; real auth secrets remain server-side.
- Installed `/etc/nginx/conf.d/youju.conf`; the full Nginx configuration test passed,
  then the shared service was gracefully reloaded. Other site files/services were
  not changed or restarted.
- Live local TLS health and static root checks returned 200 without bypassing
  certificate validation. The public API ingress returned this app's health JSON;
  `/api/auth/status` confirms credentials loaded, not successful account login.
- The public H5 **`-h` ingress** serves byte-identical built HTML, JS and CSS. Expected
  missing/private/API paths on H5 return 404. Allowed H5 preflights return 204;
  an unrelated origin returns 403; unauthenticated admin requests return 401.
- The requested public non-`-h` H5 URL returned 200 with **Welcome to nginx!** and
  missing assets, so it is explicitly **not counted as a successful app deployment**.
  The outer gateway/DNS route must be changed separately to serve this exact URL.
- Twelve production/auth/admin/foundation tests passed after fixing environment
  isolation in the new dotenv startup tests; Ruff and uv lock checks passed.
  No real login, source fetch, paid model, WeChat preview or upload was submitted.

A read-only public browser smoke and HTTP check records are saved under
`server/.artifacts/deployment-20261013/`. Operational commands:

```sh
sudo systemctl status youju-api.service
sudo systemctl restart youju-api.service  # after approved env/code changes
sudo journalctl -u youju-api.service
```

Authorization now uses zdog-auth `userinfo.permissions`, not local subject lists.
With explicit shared-service approval, load server credentials and run
`uv run flask --app youju auth-provision` to declare `youju:source:manage` and the
`youju-admin` role. Assign the role/permission to approved users in zdog-auth;
the declaration command never grants users. Remove the obsolete
`YOUJU_ADMIN_SUBJECTS` setting; it is ignored. The API must be restarted with
approval to load this code change, but subsequent central grants/revocations need
no Youju restart. With explicit approval on October 14, 2026, the central declaration succeeded
and the API was restarted; see the deployment note below.
Never send passwords, access tokens or machine secrets to obtain a grant.

### Final verification

The full server suite finished with **186 passed and two explicitly opt-in author
sandbox checks skipped**. The read-only real public-browser check rendered the H5
at the verified `-h` ingress, clicked the API health check successfully across CORS,
confirmed the anonymous admin entry was hidden, and recorded zero page errors.
No account login was submitted. All expected status checks in
`verified-ingress-checks.json` passed. The initially inspected non-`-h` default-page
responses remain in `public-http-checks.json` rather than being overwritten as
successful app checks. The API service is active with zero restarts after the final
startup, the listener is loopback-only, and the root AGENTS.md hash is unchanged.

## Later WeChat upload

The no-upload statements above describe deployment-time verification. A separate
explicit request subsequently uploaded version 0.1.0 successfully using official
Taro CI; see `WECHAT_UPLOAD.md`. Review/release and real device login remain separate.


## Central permissions rollout — October 14, 2026

The explicitly requested `auth-provision` command successfully declared the
Youju authorization model in the configured zdog-auth service, without assigning
any users. `youju-api.service` was restarted at **09:09:45 Asia/Shanghai**.
Loopback and public `youju-h.942295.xyz` checks returned health 200, configured
zdog-auth status and 401 for anonymous management requests. No source worker,
Agent or crawler was started. Real logged-in grants/device behavior remains
unverified. The existing H5 static root was not replaced.


## Managed worker deployment — October 14, 2026

The API unit now wants `youju-source-worker.service` and `youju-recipe.service`.
All three are enabled/active. Source work runs asynchronously in an API companion
process; Recipe RPC listens only at `127.0.0.1:8090` with its own native worker and
callback dispatcher. The existing public Nginx configuration exposes only `/api/`,
so internal routes remain inaccessible publicly. Independent generated RPC/callback
secrets are private to server configuration. No Nginx relaxation was needed.
See `MANAGED_DEPLOYMENT.md` for the actual installed lifecycle and verification.
