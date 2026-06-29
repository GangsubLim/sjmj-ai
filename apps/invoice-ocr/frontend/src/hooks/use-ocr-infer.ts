import * as React from "react";
import { ocrAPI } from "@/services/api";
import type { OcrJobStatus, OcrResult } from "@/types/ocr";

const POLL_MS = 2000;
const MAX_POLLS = 60; // 안전 상한(~2분)

type Status = "idle" | "pending" | "running" | "done" | "failed";

export function useOcrInfer() {
  const [status, setStatus] = React.useState<Status>("idle");
  const [result, setResult] = React.useState<OcrResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const upload = React.useCallback(async (file: File) => {
    setStatus("pending");
    setResult(null);
    setError(null);
    const { data } = await ocrAPI.createJob(file);
    const jobId = data.job_id;

    for (let i = 0; i < MAX_POLLS; i++) {
      const { data: job } = (await ocrAPI.getJob(jobId)) as {
        data: OcrJobStatus;
      };
      if (job.status === "done") {
        setResult(job.result ?? null);
        setStatus("done");
        return;
      }
      if (job.status === "failed") {
        setError(job.error ?? "추론 실패");
        setStatus("failed");
        return;
      }
      setStatus(job.status === "running" ? "running" : "pending");
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
    setError("추론 시간이 초과되었습니다.");
    setStatus("failed");
  }, []);

  return { status, result, error, upload };
}
