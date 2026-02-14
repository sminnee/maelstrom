import { invoke } from "@tauri-apps/api/core";
import type { ListAllResponse } from "../types/maelstrom";

export async function listAll(): Promise<ListAllResponse> {
  return await invoke<ListAllResponse>("list_all");
}
