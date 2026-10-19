import { call } from "@decky/api";
import { Button, Dropdown, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";

interface Caps {
  ffmpeg: string | null;
  udc: string | null;
  cards: string[];
  modes: [number, number, number][];
  can_capture: boolean;
}

interface Status {
  running: boolean;
  frames: number;
  dropped?: number;
  error?: string | null;
}

async function backend<T>(method: string, ...args: unknown[]): Promise<T> {
  return (await call(method, ...args)) as unknown as T;
}

function Content() {
  const [caps, setCaps] = useState<Caps | null>(null);
  const [status, setStatus] = useState<Status>({ running: false, frames: 0 });
  const [modeIndex, setModeIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setCaps(await backend<Caps>("capabilities"));
      setStatus(await backend<Status>("status"));
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    refresh();
    const timer = setInterval(() => {
      backend<Status>("status").then(setStatus).catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, []);

  const toggle = async () => {
    setBusy(true);
    setError(null);
    try {
      if (status.running) {
        setStatus(await backend<Status>("stop"));
      } else {
        const mode = caps?.modes[Math.min(modeIndex, (caps?.modes.length ?? 1) - 1)];
        if (!mode) throw new Error("no usable modes");
        const result = await backend<Status>("start", mode[0], mode[1], mode[2], 6);
        setStatus(result);
        if (result.error) setError(result.error);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const problem = error || status.error;

  return (
    <div style={{ padding: "0 0.5rem" }}>
      <PanelSection title="Capture card">
        <PanelSectionRow>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <Button disabled={busy || !caps?.can_capture || !caps?.modes.length}
                    onClick={toggle}>
              {status.running ? "Stop" : "Start streaming"}
            </Button>
            <Button disabled={busy || status.running} onClick={refresh}>Refresh</Button>
          </div>
        </PanelSectionRow>
        {caps && caps.modes.length > 0 && !status.running && (
          <PanelSectionRow>
            <Dropdown
              selectedOption={caps.modes[Math.min(modeIndex, caps.modes.length - 1)]}
              rgOptions={caps.modes.map((m) => ({
                label: `${m[0]}x${m[1]} @ ${m[2]}`,
                data: m,
              }))}
              onChange={(option) => setModeIndex(caps.modes.indexOf(option.data))}
            />
          </PanelSectionRow>
        )}
        {status.running && (
          <PanelSectionRow>
            <div style={{ fontSize: "0.85em", color: "var(--decky-controls-foreground)" }}>
              {status.frames} frames sent{status.dropped ? `, ${status.dropped} dropped` : ""}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {!caps?.can_capture && (
        <PanelSection title="Cannot capture">
          <PanelSectionRow>
            <div style={{ fontSize: "0.85em" }}>
              ffmpeg was not found on this Deck. It is needed to read the panel back and
              encode it.
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}

      {caps && !caps.udc && (
        <PanelSection title="No USB device controller">
          <PanelSectionRow>
            <div style={{ fontSize: "0.85em" }}>
              No UDC found, so there is nothing to present the gadget on. Streaming cannot
              start.
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}

      {problem && (
        <PanelSection title="Problem">
          <PanelSectionRow>
            <div style={{ fontSize: "0.85em" }}>{problem}</div>
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="Notes">
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", opacity: 0.7 }}>
            Experimental. The Deck appears to the host as a USB camera. Screen capture does
            not work in Game Mode, because gamescope owns the DRM master. Exit to desktop
            mode first.
          </div>
        </PanelSectionRow>
      </PanelSection>
    </div>
  );
}

export default Content;