export {};

declare global {
  interface Window {
    proposalDesktop?: {
      apiBase: string;
      showItemInFolder: (path: string) => Promise<void>;
      chooseDataDirectory: () => Promise<string | null>;
      repairRuntime: () => Promise<{ root: string; components: { name: string; installed: boolean }[] }>;
    };
  }
}
