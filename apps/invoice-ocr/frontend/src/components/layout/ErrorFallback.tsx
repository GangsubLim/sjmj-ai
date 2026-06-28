interface ErrorFallbackProps {
  error?: Error;
  resetError?: () => void;
}

export function ErrorFallback({ error, resetError }: ErrorFallbackProps) {
  return (
    <div
      role="alert"
      className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-[#F8F6F6] px-6 text-center"
    >
      <div className="flex flex-col items-center gap-2">
        <p className="text-xl font-semibold text-gray-800">
          문제가 발생했습니다
        </p>
        {error?.message && (
          <p className="text-sm text-gray-500">{error.message}</p>
        )}
      </div>
      <button
        onClick={resetError ?? (() => window.location.reload())}
        className="rounded-lg bg-[#DD6031] px-6 py-2.5 text-sm font-medium text-white focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none active:opacity-80"
      >
        다시 시도
      </button>
    </div>
  );
}
