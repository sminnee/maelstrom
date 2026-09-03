/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The orchestrator server to connect to. Unset: the in-browser fake. */
  readonly VITE_ORCHESTRATOR_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
