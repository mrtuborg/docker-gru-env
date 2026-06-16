---
name: rpm-deploy
description: Builds a Yocto package as RPM, serves it via rpm_host, and installs it on a Guro or Custom-repo device using dnf. Use when you need to install a tool or package on a device that is not in the current image (e.g. tcpdump, strace, gdbserver).
---

# Skill: Build RPM and Install on Device via rpm_host + dnf

Covers the full workflow: BitBake build → rpm_host server → dnf install on device.
Works for any Yocto-buildable package (tcpdump, strace, gdbserver, perf, etc.).

---

## Step 1 — Build the package

Use `build_c5` for Guro (imx8mp-c5-gateway), `<build-target>` for Custom-repo.

```bash
cd <your-repo-root>/custom-repo-linux
source ./env
poky run build_c5 "bitbake <package-name>"
```

Examples:
```bash
poky run build_c5 "bitbake tcpdump"
poky run build_c5 "bitbake strace"
poky run build_c5 "bitbake gdb"
poky run build_c5 "bitbake perf"
```

If the recipe name is unknown:
```bash
poky run build_c5 "bitbake-layers show-recipes | grep -i tcpdump"
# or search by file on the host:
grep -r "tcpdump" sources/*/recipes-*/ --include="*.bb" -l
```

Wait for `Build successful` before proceeding.

---

## Step 2 — Start rpm_host

```bash
cd <your-repo-root>/custom-repo-linux
source ./env
rpm_host start          # Starts Apache at http://localhost:9210
rpm_host status         # Verify it's running
```

The server serves the entire workdir volume.
RPM packages land at: `http://localhost:9210/tmp/deploy/rpm/`

---

## Step 3 — Find Mac's IP reachable from the device

The device can't reach `localhost` — it needs the Mac's LAN IP.

```bash
# Mac's IP on the same network as the device (usually en0 or en5):
ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en5 2>/dev/null
```

Expected: something like `192.168.32.x` (same subnet as the device).

If uncertain, SSH to the device and check the default route gateway:
```bash
sshpass -p <default-password> ssh root@<device-ip> "ip route | grep default"
# The gateway IP's subnet = find Mac's IP on that subnet
```

---

## Step 4 — Identify the RPM architecture subdirectory

**⚠️ The subdir names differ from what you might expect.**

rpm_host's `DocumentRoot` is `/usr/local/apache2/htdocs/yocto` (the workdir volume root).
The URL path is `http://<mac-ip>:9210/tmp/deploy/rpm/` — there is **no `/yocto/`** in the URL.

Actual subdirectory names per target (verified):

| Target | Build dir | Arch subdir |
|--------|-----------|-------------|
| Guro (imx8mp-c5-gateway) | `build_c5` | `cortexa53_crypto` |
| Custom-repo (<machine>) | `<build-target>` | `cortexa53_crypto` |
| Machine-specific (Guro) | — | `cortexa53_crypto_mx8mp` |
| Machine-specific image pkgs | — | `imx8mp_c5_gateway` |
| Arch-independent | — | `noarch` |

List available subdirs on the Mac:
```bash
curl -s http://localhost:9210/tmp/deploy/rpm/ | grep -oP 'href="\K[^"/]+'
```

---

## Step 5 — Generate repodata (REQUIRED — Yocto does NOT do this automatically)

`dnf` requires `repodata/repomd.xml` in each repo directory. Yocto does **not** generate this.
You must run `createrepo_c` inside the rpm_host container after every build.

```bash
# Install createrepo_c in the container (one-time, persists until container is recreated)
docker exec custom-repo-linux_rpm_host apt-get install -y createrepo-c -q

# Generate repodata for all arch subdirs
for SUBDIR in cortexa53_crypto cortexa53_crypto_mx8mp imx8mp_c5_gateway noarch; do
  docker exec custom-repo-linux_rpm_host \
    createrepo_c /usr/local/apache2/htdocs/yocto/tmp/deploy/rpm/$SUBDIR/ && \
    echo "✓ $SUBDIR"
done
```

You only need to run this once per build (or after adding/removing packages).

---

## Step 6 — Install on the device via dnf

**⚠️ Critical: if ANY repo in `--repofrompath` returns 404, dnf silently ignores ALL repos.**
Only specify repos that actually have `repodata/repomd.xml`. Use one `--repo` arg per valid subdir.

SSH to the device and run dnf with `--repofrompath` (no permanent config change needed):

```bash
sshpass -p <default-password> ssh root@<device-ip> "
  dnf --repofrompath=dev,http://<mac-ip>:9210/tmp/deploy/rpm/cortexa53_crypto \
      --repo=dev \
      --nogpgcheck \
      install -y <package-name>
"
```

If the package has `noarch` dependencies (check with `dnf deplist`), add a second repo:
```bash
sshpass -p <default-password> ssh root@<device-ip> "
  dnf --repofrompath=dev,http://<mac-ip>:9210/tmp/deploy/rpm/cortexa53_crypto \
      --repofrompath=dev-noarch,http://<mac-ip>:9210/tmp/deploy/rpm/noarch \
      --repo=dev --repo=dev-noarch \
      --nogpgcheck \
      install -y <package-name>
"
```

Example — install tcpdump on Guro at <device-ip>, Mac at <host-ip>:
```bash
sshpass -p <default-password> ssh root@<device-ip> "
  dnf --repofrompath=dev,http://<host-ip>:9210/tmp/deploy/rpm/cortexa53_crypto \
      --repo=dev --nogpgcheck \
      install -y tcpdump
"
```

---

## Step 7 — Verify installation

```bash
sshpass -p <default-password> ssh root@<device-ip> "which tcpdump && tcpdump --version 2>&1 | head -1"
```

---

## Step 8 — Stop rpm_host when done

```bash
cd <your-repo-root>/custom-repo-linux && source ./env && rpm_host stop
```

---

## Quick Reference

| Device | Build dir | SSH | Password |
|--------|-----------|-----|----------|
| Guro | `build_c5` | `ssh root@<device-ip>` | `<default-password>` |
| Custom-repo | `<build-target>` | via RMS jump server (use `custom-repo-device-access` skill) | varies |

| rpm_host URL pattern | Contents |
|----------------------|----------|
| `http://<mac-ip>:9210/tmp/deploy/rpm/cortexa53_crypto/` | Main packages (Guro/Custom-repo) |
| `http://<mac-ip>:9210/tmp/deploy/rpm/cortexa53_crypto_mx8mp/` | Machine-specific Guro packages |
| `http://<mac-ip>:9210/tmp/deploy/rpm/imx8mp_c5_gateway/` | Image-level Guro packages |
| `http://<mac-ip>:9210/tmp/deploy/rpm/noarch/` | Architecture-independent packages |

> **URL note:** rpm_host `DocumentRoot` = `/usr/local/apache2/htdocs/yocto` (the workdir volume).
> The URL has **no `/yocto/` prefix** — it maps directly to the volume root.

---

## Adding a Package to the Guro Image Permanently

Once a package is verified working via dnf, add it to the image recipe so it's included
in future builds. In `meta-c5-gateway`, find the image recipe (e.g. `image-guro.bb`) and
add to `IMAGE_INSTALL`:

```bitbake
IMAGE_INSTALL += "tcpdump"
```

Then rebuild and distupgrade the device (use `distupgrade-deploy` skill).

---

## Troubleshooting

**`dnf` can't reach the Mac**
- Check `rpm_host status` — is it running?
- Verify Mac's IP: `ipconfig getifaddr en0`
- Check firewall: `sudo pfctl -s rules | grep 9210` — Mac firewall might block it
- Try: `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/sbin/httpd`

**Package not found in repo**
- Verify build succeeded: `poky run build_c5 "bitbake tcpdump"` should say `Build successful`
- Check the RPM exists: `poky run build_c5 "find /workdir/tmp/deploy/rpm -name 'tcpdump*'"`
- Make sure you're using the right arch subdir — for Guro it's `cortexa53_crypto` (not `cortexa53-poky-linux`)
- **Did you run `createrepo_c`?** Without repodata, dnf can't find any packages — see Step 5

**`dnf` silently ignores all repos / "No match for argument"**
- This happens when ANY `--repofrompath` URL returns 404 — dnf drops all repos silently
- Test each repo URL manually: `curl -I http://<mac-ip>:9210/tmp/deploy/rpm/<subdir>/repodata/repomd.xml`
- Only list repos in `--repo=` that have valid repodata

**dnf says `nothing to do` or wrong version**
- Add `--disablerepo='*'` before `--repo=dev` to prevent dnf using stale cached repos
- Or: `dnf clean all` on the device first

**`Error: GPG check FAILED`**
- Always pass `--nogpgcheck` — dev builds don't have signed RPMs
