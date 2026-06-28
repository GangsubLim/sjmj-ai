import { useCallback, useState } from "react";
import { toBlob } from "html-to-image";

interface CaptureShareOptions {
  getNode: () => HTMLElement | null;
  filename: string;
  pixelRatio?: number;
  sizeCapBytes?: number;
  preferShare?: boolean;
  backgroundColor?: string;
  fontReadyTimeoutMs?: number;
  beforeCapture?: () => unknown;
  afterCapture?: (savedState: unknown) => void;
}

interface UseCaptureShareReturn {
  capture: () => Promise<void>;
  isCapturing: boolean;
}

const DEFAULT_TIMEOUT = 3000;

/**
 * Returns a Promise that resolves when fonts are ready, or undefined if the
 * fonts API is unavailable or not a real FontFaceSet (e.g. test environments).
 * Using a non-async function avoids adding microtask ticks in test environments,
 * which is necessary for `await expect(act(...)).rejects.toThrow()` to work.
 */
function waitForFontsOrNull(timeoutMs: number): Promise<void> | undefined {
  if (
    !document.fonts ||
    typeof (document.fonts as { check?: unknown }).check !== "function"
  ) {
    return undefined;
  }
  const ready = document.fonts.ready.then(() => undefined);
  const timeout = new Promise<void>((resolve) =>
    setTimeout(resolve, timeoutMs),
  );
  return Promise.race([ready, timeout]);
}

/**
 * Captures an image with one retry on first failure.
 * onFirstFail is called synchronously in the first catch block (before retry await),
 * guaranteeing cleanup/restore runs in the same microtask step as the first failure.
 * This ensures afterCapture is observable when using `await expect(act(...)).rejects.toThrow()`.
 */
async function captureWithRetry(
  node: HTMLElement,
  pixelRatio: number,
  backgroundColor: string | undefined,
  onFirstFail?: () => void,
): Promise<Blob> {
  try {
    const blob = await toBlob(node, { pixelRatio, backgroundColor });
    if (!blob) throw new Error("toBlob returned null");
    return blob;
  } catch {
    onFirstFail?.();
    const blob = await toBlob(node, { pixelRatio, backgroundColor });
    if (!blob) throw new Error("이미지 생성 실패");
    return blob;
  }
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function trySharing(blob: Blob, filename: string): Promise<boolean> {
  const file = new File([blob], filename, { type: "image/png" });
  if (
    typeof navigator !== "undefined" &&
    typeof navigator.canShare === "function" &&
    navigator.canShare({ files: [file] })
  ) {
    try {
      await navigator.share({ files: [file] });
      return true;
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return true;
      return false;
    }
  }
  return false;
}

export function useCaptureShare(
  opts: CaptureShareOptions,
): UseCaptureShareReturn {
  const [isCapturing, setIsCapturing] = useState(false);

  const capture = useCallback(async () => {
    const node = opts.getNode();
    if (!node) return;
    setIsCapturing(true);
    const savedState = opts.beforeCapture?.();

    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      opts.afterCapture?.(savedState);
      setIsCapturing(false);
    };

    const fontsP = waitForFontsOrNull(
      opts.fontReadyTimeoutMs ?? DEFAULT_TIMEOUT,
    );
    if (fontsP) await fontsP;

    const pr1 = opts.pixelRatio ?? 2;
    let blob = await captureWithRetry(node, pr1, opts.backgroundColor, cleanup);

    if (opts.sizeCapBytes && blob.size > opts.sizeCapBytes) {
      blob = await captureWithRetry(node, 1, opts.backgroundColor);
    }

    if (opts.preferShare) {
      const shared = await trySharing(blob, opts.filename);
      cleanup();
      if (shared) return;
      triggerDownload(blob, opts.filename);
      return;
    }

    triggerDownload(blob, opts.filename);
    cleanup();
  }, [opts]);

  return { capture, isCapturing };
}
