import * as React from "react";
import { StampIcon, Trash2Icon, ImageIcon } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { validateFileSize } from "@/utils/validators";
import { Button } from "@/components/ui/button";

interface StampUploadProps {
  value?: string;
  onChange: (url: string | undefined) => void;
  className?: string;
}

function StampUpload({ value, onChange, className }: StampUploadProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.match(/^image\/(png|jpe?g)$/)) {
      toast.error("PNG 또는 JPG 파일만 업로드 가능합니다");
      return;
    }
    if (!validateFileSize(file, 500)) {
      toast.error("파일 크기는 500KB 이하여야 합니다");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => onChange(reader.result as string);
    reader.readAsDataURL(file);
  };

  if (value) {
    return (
      <div className={cn("space-y-2", className)}>
        <div className="border-primary/30 bg-primary/5 flex items-center justify-center rounded-lg border-2 border-dashed p-6">
          <img
            src={value}
            alt="도장"
            className="max-h-24 object-contain"
            width={96}
            height={96}
          />
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={() => inputRef.current?.click()}
          >
            변경
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onChange(undefined)}
            aria-label="도장 삭제"
          >
            <Trash2Icon className="size-4" />
          </Button>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg"
          className="hidden"
          onChange={handleFile}
        />
      </div>
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="border-primary/30 bg-primary/5 hover:bg-primary/10 flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 transition-colors"
      >
        <div className="bg-primary/10 text-primary flex size-16 items-center justify-center rounded-full">
          <StampIcon className="size-8" aria-hidden="true" />
        </div>
        <span className="text-foreground text-sm font-medium">
          도장 이미지 업로드
        </span>
        <span className="text-muted-foreground text-xs">
          PNG/JPG, 500KB 이하
        </span>
      </button>
      <div className="text-muted-foreground flex items-center gap-2 text-xs">
        <ImageIcon className="size-3" aria-hidden="true" />
        <span>기본 도장: /images/sjdojang.png</span>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg"
        className="hidden"
        onChange={handleFile}
      />
    </div>
  );
}

export { StampUpload };
