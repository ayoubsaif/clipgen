# clipgen local API

This API connects the desktop interface to the Python engine. The full request and response schema is in [openapi.json](openapi.json). Regenerate it after changing the API:

```powershell
uv run --extra gpu clipgen api --export-openapi docs/openapi.json
```

## Start it

```powershell
uv run --extra gpu clipgen api
```

The first line printed is JSON with the address and a token that is new on each launch:

```json
{"url": "http://127.0.0.1:8765", "token": "..."}
```

The desktop app will read this line when it starts the engine. Send the token on every request:

```
Authorization: Bearer <token>
```

## Security rules

- It listens on `127.0.0.1` only.
- Every route except `/api/health` needs the token. With `--ui`, the page and its built assets are also public because they hold no secrets, and they are served with a strict Content-Security-Policy.
- Requests with another `Host`, or from a website outside the allowed origins, get `403`. The allowed origins are Tauri's, the Vite dev server on port 5173, and the API's own address when `--ui` is on.
- `clipgen app` passes the token in the URL fragment, which is never sent to a server. The page moves it to session storage and clears it from the address.
- `video_url` and frame `url` values are signed links for `<video>` and `<img>`. They last 6 hours and work only for that file.
- Credentials are write-only. Responses report their status and never their value.
- The assistant only proposes changes. They run only through `assistant/actions/apply`, using the ID of a stored proposal.

## Posting rules

The server enforces these for every interface:

- Only approved, rendered clips can be sent.
- `schedule` and `now` return `428` unless `confirm` is `true`.
- Sending a clip again returns `428` unless `resend` is `true`.
- Channels must be connected in Postiz. Without a visibility, YouTube is private and TikTok goes to the inbox.

## Main routes

| Area | Routes |
|---|---|
| App | `GET /api/health`, `GET /api/meta` |
| Videos | `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/transcript`, `POST /api/runs/{id}/render` |
| Clips | `PATCH /api/runs/{id}/clips/{n}`, `PUT .../decision`, `POST /api/runs/{id}/clips/from-captions`, `GET .../video` |
| Jobs | `GET` and `POST /api/jobs`, `GET /api/jobs/{id}`, `POST .../cancel`, `GET .../log`, `GET /api/events` (live updates) |
| Webcam | `GET` and `PUT /api/runs/{id}/webcam`, `POST .../detect`, `GET .../frames/{i}` |
| Assistant | `GET` and `DELETE /api/runs/{id}/assistant`, `POST .../messages`, `POST .../actions/apply`, `POST .../actions/{action}/discard` |
| Credentials | `GET /api/credentials`, `PUT` and `DELETE /api/credentials/{name}` |
| Posting | `GET` and `PUT /api/postiz/settings`, `GET /api/postiz/channels`, `GET /api/runs/{id}/posts`, `POST /api/runs/{id}/clips/{n}/post` |

`/api/events` is a server-sent event stream that needs the `Authorization` header, so read it with `fetch` rather than `EventSource`.
