import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from '../api/ApiProvider';
import type { ApiClient } from '../api/http';
import type { Attachment } from '../api/attachments';
import { AttachField } from './AttachField';

const PNG = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', {
  type: 'image/png',
});

function stored(name = 'shot.png'): Attachment {
  return {
    name,
    markdown: `![${name}]({{MAEL_TASK_DIR}}/images/t1/${name})`,
    url: `/api/attachments/northwind/t1/${name}`,
  };
}

/** The field over a fake API, with the caller's state held as a test double. */
function setup(attached: Attachment[] = [], props: { disabled?: boolean } = {}) {
  const post = vi.fn().mockResolvedValue({
    markdown: stored().markdown,
    url: stored().url,
  });
  const api = { post } as unknown as ApiClient;
  const onAttach = vi.fn();
  const onRemove = vi.fn();
  render(
    <ApiProvider
      api={api}
      queryClient={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}
    >
      <AttachField
        project="northwind"
        bucket="t1"
        attached={attached}
        onAttach={onAttach}
        onRemove={onRemove}
        {...props}
      >
        <textarea aria-label="What needs doing?" />
      </AttachField>
    </ApiProvider>,
  );
  return { post, onAttach, onRemove };
}

describe('AttachField', () => {
  it('uploads a picked image and hands back the ref to append', async () => {
    const user = userEvent.setup();
    const { post, onAttach } = setup();

    await user.upload(screen.getByLabelText('Attach image', { selector: 'input' }), PNG);

    await waitFor(() => expect(onAttach).toHaveBeenCalledTimes(1));
    // The markdown ref carries the portable token, never the fetch URL: a task
    // stores this, and the agent reads it from disk.
    expect((onAttach.mock.calls[0]?.[0] as Attachment).markdown).toContain('{{MAEL_TASK_DIR}}');
    const [path, body] = post.mock.calls[0] as [string, FormData];
    expect(path).toBe('/api/attachments');
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('project')).toBe('northwind');
    expect(body.get('bucket')).toBe('t1');
  });

  it('uploads an image pasted from the clipboard', async () => {
    const user = userEvent.setup();
    const { onAttach } = setup();

    await user.click(screen.getByLabelText('What needs doing?'));
    await user.paste({ files: [PNG] } as unknown as DataTransfer);

    await waitFor(() => expect(onAttach).toHaveBeenCalledTimes(1));
  });

  it('leaves a text paste to the textarea', async () => {
    const user = userEvent.setup();
    const { post } = setup();
    const textarea = screen.getByLabelText('What needs doing?');

    await user.click(textarea);
    await user.paste('just some words');

    expect(post).not.toHaveBeenCalled();
    expect(textarea).toHaveValue('just some words');
  });

  it('offers each attached image for removal', async () => {
    const user = userEvent.setup();
    const { onRemove } = setup([stored()]);

    await user.click(screen.getByRole('button', { name: 'Remove shot.png' }));

    // The whole image, not just its url: the caller has to strip its markdown
    // ref from the text as well as drop the thumbnail.
    expect(onRemove).toHaveBeenCalledWith(stored());
  });

  it('reports every failure of a multi-file pick, not just the last', async () => {
    const user = userEvent.setup();
    const { post } = setup();
    post
      .mockRejectedValueOnce(new Error('too large'))
      .mockRejectedValueOnce(new Error('not an image'));
    const second = new File([new Uint8Array([0x89])], 'other.png', { type: 'image/png' });

    await user.upload(screen.getByLabelText('Attach image', { selector: 'input' }), [PNG, second]);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('shot.png: too large');
    expect(alert).toHaveTextContent('other.png: not an image');
  });

  it('ignores a paste when the field is disabled', async () => {
    const { post } = setup([], { disabled: true });

    // Fired directly: user-event's paste needs a full clipboard stub, and the
    // handler sits on the wrapper rather than the textarea.
    fireEvent.paste(screen.getByLabelText('What needs doing?'), {
      clipboardData: { files: [PNG], getData: () => '' },
    });

    // The agent has exited; nothing should be written to the task repo for it.
    expect(post).not.toHaveBeenCalled();
  });

  it('names the file that failed to upload', async () => {
    const user = userEvent.setup();
    const { post, onAttach } = setup();
    post.mockRejectedValue(new Error('Attachment is too large'));

    await user.upload(screen.getByLabelText('Attach image', { selector: 'input' }), PNG);

    expect(await screen.findByRole('alert')).toHaveTextContent('shot.png: Attachment is too large');
    expect(onAttach).not.toHaveBeenCalled();
  });
});
