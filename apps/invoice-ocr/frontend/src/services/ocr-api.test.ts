import api, { ocrAPI } from "./api";

describe("ocrAPI", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("createJob posts multipart form with photo field and returns parsed data", async () => {
    const mockData = { success: true, data: { job_id: 1, status: "pending" } };
    vi.spyOn(api, "post").mockResolvedValue({ data: mockData });

    const file = new File([new Uint8Array([1, 2, 3])], "scan.jpg", {
      type: "image/jpeg",
    });
    const res = await ocrAPI.createJob(file);

    expect(api.post).toHaveBeenCalledWith(
      "/ocr/jobs",
      expect.any(FormData),
      expect.objectContaining({
        headers: { "Content-Type": "multipart/form-data" },
      }),
    );
    expect(res.data.job_id).toBe(1);
  });

  it("getJob fetches job status by id", async () => {
    const mockData = { success: true, data: { id: 1, status: "done" } };
    vi.spyOn(api, "get").mockResolvedValue({ data: mockData });

    const res = await ocrAPI.getJob(1);

    expect(api.get).toHaveBeenCalledWith("/ocr/jobs/1");
    expect(res.data.id).toBe(1);
    expect(res.data.status).toBe("done");
  });

  it("confirm posts payload to job confirm endpoint", async () => {
    const mockData = { success: true, data: { invoice_id: 42 } };
    vi.spyOn(api, "post").mockResolvedValue({ data: mockData });

    const payload = { rows: [] };
    const res = await ocrAPI.confirm(1, payload);

    expect(api.post).toHaveBeenCalledWith("/ocr/jobs/1/confirm", payload);
    expect(res.data.invoice_id).toBe(42);
  });
});
