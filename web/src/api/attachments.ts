import { useMutation } from '@tanstack/react-query';
import { useApi } from './ApiProvider';
import { SLOW_CALL_TIMEOUT_MS } from './http';

/** One image, once the server has stored it. */
export interface Attachment {
  /** The name the user's file had, for the alt text and the thumbnail label. */
  name: string;
  /**
   * The markdown ref to append to the text. It holds the portable
   * `{{MAEL_TASK_DIR}}` token for a task, which the agent later reads from
   * disk — never the URL, which only this server can serve.
   */
  markdown: string;
  /** Where the browser fetches the bytes: the thumbnail and the transcript. */
  url: string;
}

interface UploadReply {
  markdown: string;
  url: string;
}

/**
 * Put one image in the task repo.
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
      return { name: vars.file.name, ...reply };
    },
  });
}

/**
 * The text with one attachment's markdown ref taken out.
 *
 * Removing a thumbnail has to remove the ref too. Left behind, it goes to the
 * agent as a link to an image that was never sent, and shows in the transcript
 * as a broken image.
 */
export function withoutRef(text: string, image: Attachment): string {
  return text
    .split(image.markdown)
    .join('')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
