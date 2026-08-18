# Host power — running an app that needs a real device

Some apps cannot be built out of a process. An app that runs a **guest** —
a Windows VM, an Android image — needs kernel facilities that no amount of
userspace code can substitute:

- without `/dev/kvm`, QEMU falls back to software emulation and a Windows
  guest is unusably slow;
- without `/dev/net/tun`, that guest has no network interface at all;
- an Android guest needs the binder IPC devices.

A Tier-2 app container gets none of those by default, and that default is
correct: an app container is unprivileged, holds no extra Linux capabilities,
and sees no host devices. `runtime.host_power` is the narrow, three-way-gated
exception.

## The three legs

A grant only takes effect when all three line up, and no single party can
arrange them alone:

| Leg | Who decides | Where |
|---|---|---|
| 1. the app **asks** | the app author | `runtime.host_power` in `aw-app.json` |
| 2. the app is **allowed** to ask | the marketplace (signing) | a `host:*` permission, all **high risk** |
| 3. the machine **offers** it | whoever owns the machine | `aw-remote-host --host-power=…` |

Leg 3 is the one that matters most and the one an app has no say in. The
person running the BYOD host decides how much access that machine hands out;
an app can only state what it needs.

**A missing leg fails the install**, with a message naming which leg and what
to do about it. It does *not* start the container without the grant — a
Windows VM that comes up in software emulation reads as "this app is broken
and slow", and the actual cause (a host that never opted in) is invisible from
there.

## Grants

| Grant | Gives the container | Permission |
|---|---|---|
| `kvm` | `/dev/kvm` | `host:device-kvm` |
| `tun` | `/dev/net/tun` + `NET_ADMIN` | `host:device-tun` |
| `fuse` | `/dev/fuse` + `SYS_ADMIN` | `host:device-fuse` |
| `binder` | `/dev/binder`, `/dev/hwbinder`, `/dev/vndbinder` | `host:device-binder` |
| `privileged` | everything, no isolation | `host:privileged` |
| `all` | every grant above **except** `privileged` | (each one's) |

`privileged` on a **host** satisfies any narrower app request — it grants every
device and capability, so refusing an app that asked for `kvm` would make the
most permissive setting the one under which the app won't install. The app still
receives only what it declared: ask for `kvm`+`tun` on a `privileged` host and
the container gets those two devices, not `--privileged`. The host's grant is a
ceiling, not a floor. The implication is one-way — `all` never satisfies an app
that explicitly asked for `privileged`.

`all` deliberately excludes `privileged`. "Every device this host can offer"
and "dissolve the container boundary" are different decisions with very
different blast radii, and a convenience keyword must not make the second one
on someone's behalf. `privileged` has to be typed by name, and
`aw-remote-host` asks for confirmation before granting it even under `--yes`.

Some grants pair a device with a capability because the device alone is
useless: `/dev/net/tun` opens fine without `NET_ADMIN` and then fails to
configure the interface, producing a guest with a NIC that carries no traffic.

## Declaring it

```jsonc
{
  "id": "aw-app-windows",
  "tier": "container",                  // Tier-1 apps cannot be elevated — see below
  "runtime": {
    "image": "dockurr/windows",
    "port": 8006,
    "host_power": ["kvm", "tun"]        // ask for exactly what you need
  },
  "permissions": [
    "containers:manage",
    "host:device-kvm",                  // leg 2 — one per grant
    "host:device-tun"
  ]
}
```

Ask for the specific grants, not `all`. `all` on an app manifest requests four
device grants and makes the app uninstallable on any host that cannot provide
all four — including hosts that could have run it perfectly with two.

**Only `tier: container` apps.** A Tier-1 app runs inside the workspace
process and already has exactly the workspace's own access, so a `host_power`
key there would read as a privilege and change nothing. Declaring it is a
manifest error rather than a silent no-op.

**Sidecars are not covered yet.** `runtime.sidecars[].host_power` is rejected
at validation rather than ignored, because tolerating it would start a
companion container without the device it asked for while the manifest read as
correct.

## Enabling it on a host

```bash
# grant specific devices
aw-remote-host bootstrap-workspace --with-workspace --host-power=kvm,tun

# every device grant, without dropping isolation
aw-remote-host bootstrap-workspace --with-workspace --host-power=all

# revoke — explicit, because omitting the flag leaves the current grant alone
aw-remote-host bootstrap-workspace --with-workspace --host-power=none
```

Each grant is **probed**, not assumed. A request is not a grant: there is no
`/dev/kvm` on macOS, and rootless podman cannot pass through a device the
invoking user cannot open. `--host-power=all` on a machine with no binder
devices grants the other three and says what it dropped.

Check what actually took effect:

```bash
aw-remote-host status          # requested -> effective, with a reason per refusal
aw-workspace-cli doctor        # from inside the workspace: grants, and who uses them
```

…and in aw-console, **Workspace › Manage** shows a **Host power** badge on the
remote-host panel: neutral `Standard`, amber for device grants, red for
`Privileged`, plus a line naming anything requested that the machine could not
deliver. That delta is the point — a host can believe it enabled KVM and have
not, and the only other symptom is a guest VM that is inexplicably slow.

## Where the code is

| Piece | File |
|---|---|
| grant catalog + the three-leg check | aw-workspace `src/apps/hostpower.py` |
| manifest validation | aw-workspace `src/apps/manifest.py` (`_validate_host_power`) |
| the `podman`/docker run call | aw-workspace `src/apps/containers.py` |
| host probe + `--host-power` flag | aw-remote-host `internal/hostpower/` |
| the badge | aw-console `src/components/byod-panel.tsx` |

The grant **names** are a wire contract between the Go probe and the Python
enforcement (they cross the boundary as `AW_HOST_POWER`). Both sides have a
test asserting the catalog matches; renaming one side silently stops matching
the other.
