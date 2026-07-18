/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_OBJECT_STORE_ORIGINS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
