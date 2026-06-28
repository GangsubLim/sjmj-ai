import * as React from "react";
import {
  XIcon,
  MaximizeIcon,
  DownloadIcon,
  PrinterIcon,
  ImageIcon,
  LoaderIcon,
} from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";

import type { Invoice } from "@/types/invoice";
import type { Issuer } from "@/types/settings";
import { Button } from "@/components/ui/button";
import { ZoomControls } from "@/components/ui/zoom-controls";
import { InvoiceDocument } from "./invoice-document";
import { useCaptureShare } from "@/hooks/use-capture-share";

interface InvoicePreviewProps {
  invoice: Invoice;
  issuer: Issuer;
  onClose: () => void;
}

function InvoicePreview({ invoice, issuer, onClose }: InvoicePreviewProps) {
  const documentRef = React.useRef<HTMLDivElement>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const [scale, setScale] = React.useState(1);
  const [isDragging, setIsDragging] = React.useState(false);
  const [isImageLoading, setIsImageLoading] = React.useState(false);
  const [dragStart, setDragStart] = React.useState({ x: 0, y: 0 });
  const [scrollStart, setScrollStart] = React.useState({ x: 0, y: 0 });

  // Calculate initial scale to fit width
  React.useEffect(() => {
    const calculateScale = () => {
      if (scrollRef.current) {
        const containerWidth = scrollRef.current.clientWidth;
        const documentWidth = 635;
        const isMobile = window.innerWidth <= 768;
        const padding = isMobile ? 20 : 40;
        setScale(Math.min((containerWidth - padding) / documentWidth, 1));
      }
    };
    calculateScale();
    window.addEventListener("resize", calculateScale);
    return () => window.removeEventListener("resize", calculateScale);
  }, []);

  const handleFitScreen = () => {
    if (scrollRef.current) {
      const containerWidth = scrollRef.current.clientWidth;
      const documentWidth = 635;
      const isMobile = window.innerWidth <= 768;
      const padding = isMobile ? 20 : 40;
      setScale(Math.min((containerWidth - padding) / documentWidth, 1));
    }
  };

  const captureOptions = { pixelRatio: 2, backgroundColor: "#ffffff" } as const;

  // Temporarily remove parent scale transform so html-to-image captures full size
  const withUnscaledCapture = async <T,>(fn: () => Promise<T>): Promise<T> => {
    const parent = documentRef.current?.parentElement as HTMLElement | null;
    const saved = parent?.style.transform ?? "";
    if (parent) parent.style.transform = "none";
    void documentRef.current!.offsetHeight; // force reflow
    try {
      return await fn();
    } finally {
      if (parent) parent.style.transform = saved;
    }
  };

  const getFilename = (ext: string) =>
    `거래명세서_${invoice.recipient}_${format(new Date(invoice.issue_date), "yyyyMMdd")}.${ext}`;

  const imageCapture = useCaptureShare({
    getNode: () => documentRef.current,
    filename: getFilename("png"),
    pixelRatio: 2,
    backgroundColor: "#ffffff",
    preferShare: true,
    beforeCapture: () => {
      const parent = documentRef.current?.parentElement as HTMLElement | null;
      const saved = parent?.style.transform ?? "";
      if (parent) parent.style.transform = "none";
      void documentRef.current?.offsetHeight;
      return { parent, saved };
    },
    afterCapture: (state) => {
      const s = state as { parent: HTMLElement | null; saved: string };
      if (s?.parent) s.parent.style.transform = s.saved;
    },
  });

  const handleDownloadPDF = async () => {
    if (!documentRef.current) return;
    try {
      await document.fonts.ready;
      const { toCanvas } = await import("html-to-image");
      const { default: jsPDF } = await import("jspdf");
      const canvas = await withUnscaledCapture(() =>
        toCanvas(documentRef.current!, captureOptions),
      );
      const pdf = new jsPDF({
        orientation: "portrait",
        unit: "mm",
        format: "a4",
      });
      const pageW = pdf.internal.pageSize.getWidth();
      const pageH = pdf.internal.pageSize.getHeight();
      const margin = 10;
      const printableW = pageW - 2 * margin;
      const printableH = pageH - 2 * margin;
      // Width-based scaling to preserve readability
      const ratio = printableW / canvas.width;
      const scaledH = canvas.height * ratio;

      if (scaledH <= printableH) {
        // Single page — fits within A4
        const imgData = canvas.toDataURL("image/jpeg", 0.98);
        pdf.addImage(imgData, "JPEG", margin, margin, printableW, scaledH);
      } else {
        // Multi-page — slice canvas into page-sized chunks
        const pageCanvasH = printableH / ratio;
        const totalPages = Math.ceil(canvas.height / pageCanvasH);

        for (let i = 0; i < totalPages; i++) {
          if (i > 0) pdf.addPage();
          const srcY = i * pageCanvasH;
          const srcH = Math.min(pageCanvasH, canvas.height - srcY);
          const destH = srcH * ratio;
          const slice = document.createElement("canvas");
          slice.width = canvas.width;
          slice.height = srcH;
          const ctx = slice.getContext("2d");
          if (!ctx) continue;
          ctx.drawImage(
            canvas,
            0,
            srcY,
            canvas.width,
            srcH,
            0,
            0,
            canvas.width,
            srcH,
          );
          const pageImg = slice.toDataURL("image/jpeg", 0.98);
          pdf.addImage(pageImg, "JPEG", margin, margin, printableW, destH);
        }
      }

      pdf.save(getFilename("pdf"));
    } catch (err) {
      console.error("PDF 다운로드 실패:", err);
      toast.error("PDF 다운로드에 실패했습니다. 다시 시도해주세요.");
    }
  };

  const handleDownloadImage = async () => {
    setIsImageLoading(true);
    try {
      await imageCapture.capture();
    } catch (err) {
      console.error("이미지 다운로드 실패:", err);
      toast.error("이미지 다운로드에 실패했습니다. 다시 시도해주세요.");
    } finally {
      setIsImageLoading(false);
    }
  };

  const handlePrint = () => {
    window.print();
  };

  // Mouse drag scroll
  const handleMouseDown = (e: React.MouseEvent) => {
    if (scale > 1) {
      setIsDragging(true);
      setDragStart({ x: e.clientX, y: e.clientY });
      if (scrollRef.current) {
        setScrollStart({
          x: scrollRef.current.scrollLeft,
          y: scrollRef.current.scrollTop,
        });
      }
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging && scrollRef.current) {
      scrollRef.current.scrollLeft = scrollStart.x + (dragStart.x - e.clientX);
      scrollRef.current.scrollTop = scrollStart.y + (dragStart.y - e.clientY);
    }
  };

  const handleMouseUp = () => setIsDragging(false);

  // Ctrl+wheel zoom
  const handleWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.1 : 0.1;
      setScale((prev) => Math.min(Math.max(prev + delta, 0.3), 3));
    }
  };

  // Escape key handler
  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  // Focus trap
  const previousFocusRef = React.useRef<HTMLElement | null>(null);
  const modalRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement;
    const firstFocusable = modalRef.current?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    firstFocusable?.focus();

    return () => {
      previousFocusRef.current?.focus();
    };
  }, []);

  // Tab key trapping — keep focus within modal
  React.useEffect(() => {
    const handleTabTrap = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusable = modalRef.current?.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleTabTrap);
    return () => document.removeEventListener("keydown", handleTabTrap);
  }, []);

  return (
    <div
      ref={modalRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="preview-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50 print:static print:bg-transparent"
    >
      <div className="bg-background flex h-[90vh] w-[90%] max-w-[1200px] flex-col rounded-lg shadow-xl max-md:h-dvh max-md:w-full max-md:rounded-none print:h-auto print:w-full print:shadow-none">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b px-4 py-3 print:hidden">
          <h2 id="preview-title" className="truncate text-base font-semibold">
            거래명세서 미리보기
          </h2>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleFitScreen}
              title="화면에 맞추기"
            >
              <MaximizeIcon className="mr-1 size-4" />
              맞춤
            </Button>
            <Button variant="ghost" size="sm" onClick={handleDownloadPDF}>
              <DownloadIcon className="mr-1 size-4" />
              PDF
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDownloadImage}
              disabled={isImageLoading}
            >
              {isImageLoading ? (
                <LoaderIcon
                  className="mr-1 size-4 animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <ImageIcon className="mr-1 size-4" aria-hidden="true" />
              )}
              이미지
            </Button>
            <Button variant="ghost" size="sm" onClick={handlePrint}>
              <PrinterIcon className="mr-1 size-4" />
              인쇄
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onClose}
              aria-label="닫기"
            >
              <XIcon className="size-5" />
            </Button>
          </div>
        </div>

        {/* Scroll container */}
        <div
          ref={scrollRef}
          className="bg-pdf-canvas flex-1 overflow-auto overscroll-contain p-5 select-none print:overflow-visible print:bg-white print:p-0"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          style={{
            cursor: isDragging ? "grabbing" : scale > 1 ? "grab" : "default",
          }}
        >
          <div
            className="flex min-h-max min-w-max origin-top-left items-start justify-center p-5 transition-transform duration-200 print:!transform-none print:p-0"
            style={{ transform: `scale(${scale})` }}
          >
            <InvoiceDocument
              ref={documentRef}
              invoice={invoice}
              issuer={issuer}
            />
          </div>
        </div>

        {/* Zoom controls */}
        <ZoomControls
          onZoomIn={() => setScale((prev) => Math.min(prev + 0.1, 3))}
          onZoomOut={() => setScale((prev) => Math.max(prev - 0.1, 0.3))}
          scale={scale}
          className="bottom-8 print:hidden"
        />
      </div>
    </div>
  );
}

export { InvoicePreview };
