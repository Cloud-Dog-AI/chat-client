# External Clone Self-Contained — chat-client (§7, W28A-580 closure)

**Question:** Is a fresh external (public) clone of this repository self-contained
for building the `chat-client` service — i.e. can it build with **no hidden git
submodules or sibling repositories**?

**Verdict: YES — self-contained.**

## Evidence (verified 2026-06-07, branch fix/W28A-861-R3-chat-client)

1. **The chat-client build references zero submodules.** Neither `Dockerfile.public`
   nor `Dockerfile.chat-client` nor `docker-build.sh` references `third_party/`,
   `Example-MCP-Server`, `modelcontextprotocol`, or any `submodule`:

   ```
   $ grep -lE 'third_party|Example-MCP|submodule|modelcontextprotocol' \
       Dockerfile.public Dockerfile.chat-client docker-build.sh
   (no matches)
   ```

   The image `COPY`s only `src/`, the pre-built `ui/`, `migrations/`,
   `database/migrations/`, configs, and the start/entrypoint scripts.

2. **The submodules are excluded from the image.** `.dockerignore` lists
   `third_party/`, so even a fully-initialised checkout never ships submodule
   content into the image.

3. **The three declared submodules are PUBLIC GitHub repos, not internal Cloud-Dog
   siblings** (`.gitmodules`):
   - `third_party/modelcontextprotocol-servers` → `github.com/modelcontextprotocol/servers`
   - `third_party/example-remote-server` → `github.com/modelcontextprotocol/example-remote-server`
   - `Example-MCP-Server` → `github.com/danny-avila/Example-MCP-Server`

   They exist only as optional helpers for building unrelated MCP-server demo images
   and are **not** part of the chat-client application. None resolves to
   `git.cloud-dog.net`, GitLab, or any internal host.

4. **A fresh clone leaves them uninitialised and empty** (gitlinks only):

   ```
   $ git submodule status
   -a263ec9... Example-MCP-Server
   -f48f411... third_party/example-remote-server
   -3fd7fb6... third_party/modelcontextprotocol-servers
   ```

   The leading `-` means "not checked out". `git clone <repo>` (without
   `--recurse-submodules`) is sufficient to build chat-client. No
   `git submodule update --init` is required.

5. **W28A-580 closure.** The prior finding W28A-580 changed the default build target
   from `everything` (which tried to build the submodule-backed MCP demo images) to
   `chat-client` only, eliminating the submodule dependency for the default build.
   This R3 lane preserves that: `docker-build.sh` builds **only** the chat-client
   image (`Dockerfile.public` / `Dockerfile.chat-client`), never a submodule target.

## Conclusion

A fresh public clone builds chat-client end-to-end without initialising any
submodule and without any sibling repository. The submodule references are inert,
public, and optional. **Self-contained: YES.**
