import { useMutation } from '@tanstack/react-query';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';

/** An `image` is embedded; a `file` is linked. See docs/dev/orchestrator-server.md. */
export type AttachmentKind = 'image' | 'file';

/** One file, once the server has stored it. */
export interface Attachment {
  /** The name the user's file had, for the alt text and the strip's label. */
  name: string;
  /** The file's size in bytes, for the strip's label. */
  size: number;
  kind: AttachmentKind;
  /**
   * The markdown to append to the text: an image ref, or a link for a file.
   * It holds the portable `{{MAEL_TASK_DIR}}` token, which the agent later
   * reads from disk — never the URL, which only this server can serve.
   */
  markdown: string;
  /** Where the browser fetches the bytes: the thumbnail and the transcript. */
  url: string;
}

interface UploadReply {
  markdown: string;
  url: string;
  kind: AttachmentKind;
}

/**
 * Put one file in its Bucket.
 *
 * See `docs/dev/orchestrator-server.md` for why this is multipart, and why
 * uploading is separate from sending.
 */
export function useUploadAttachment() {
  const api = useApi();
  return useMutation({
    mutationFn: async (vars: {
      project: string;
      bucket: string;
      file: File;
    }): Promise<Attachment> => {
      const form = new FormData();
      form.append('project', vars.project);
      form.append('bucket', vars.bucket);
      form.append('file', vars.file, vars.file.name);
      const reply = await api.post<UploadReply>('/api/attachments', form, {
        timeoutMs: SLOW_CALL_TIMEOUT_MS,
      });
      return { name: vars.file.name, size: vars.file.size, ...reply };
    },
  });
}

/**
 * The text with one attachment's markdown ref taken out.
 *
 * Removing an item from the strip has to remove its ref too. Left behind, the
 * ref still reaches the agent and the transcript after the user took it off.
 */
export function withoutRef(text: string, attachment: Attachment): string {
  return text
    .split(attachment.markdown)
    .join('')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
