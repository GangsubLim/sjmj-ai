import { useEffect } from "react";
import { useSettingsStore } from "@/stores/settings-store";

export function useSettings() {
  const store = useSettingsStore();
  const { isLoaded, fetchIssuer, fetchAppSettings } = store;

  useEffect(() => {
    if (!isLoaded) {
      fetchIssuer();
      fetchAppSettings();
    }
  }, [isLoaded, fetchIssuer, fetchAppSettings]);

  return store;
}
