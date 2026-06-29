import { create } from "zustand";
import type { Issuer, AppSettings } from "@/types/settings";
import { DEFAULT_APP_SETTINGS } from "@/types/settings";
import { settingsAPI } from "@/services/api";

interface SettingsStore {
  issuer: Issuer | null;
  appSettings: AppSettings;
  isLoaded: boolean;
  fetchIssuer: () => Promise<void>;
  updateIssuer: (issuer: Issuer) => Promise<void>;
  fetchAppSettings: () => Promise<void>;
  updateAppSettings: (settings: Partial<AppSettings>) => Promise<void>;
}

export const useSettingsStore = create<SettingsStore>((set, get) => ({
  issuer: null,
  appSettings: DEFAULT_APP_SETTINGS,
  isLoaded: false,

  fetchIssuer: async () => {
    try {
      const res = await settingsAPI.getIssuer();
      set({ issuer: res.data, isLoaded: true });
    } catch (e) {
      console.error("Failed to fetch issuer:", e);
      set({ isLoaded: true });
    }
  },

  updateIssuer: async (issuer: Issuer) => {
    const res = await settingsAPI.saveIssuer(issuer);
    set({ issuer: res.data });
  },

  fetchAppSettings: async () => {
    try {
      const res = await settingsAPI.getAppSettings();
      set({ appSettings: res.data });
    } catch (e) {
      console.error("Failed to fetch app settings:", e);
    }
  },

  updateAppSettings: async (settings: Partial<AppSettings>) => {
    const current = get().appSettings;
    const res = await settingsAPI.updateAppSettings({
      ...current,
      ...settings,
    });
    set({ appSettings: res.data });
  },
}));
