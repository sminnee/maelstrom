import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from '../api/ApiProvider';
import type { ApiClient } from '../api/http';
import type { Attachment } from '../api/attachments';
import { AttachField } from './AttachField';
import { Dialog } from './Dialog';

const PNG = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', {
  type: 'image/png',
});

function stored(name = 'shot.png'): Attachment {
  return {
    name,
    size: 4,
    kind: 'image',
    markdown: `![${name}]({{MAEL_TASK_DIR}}/images/t1/${name})`,
    url: `/api/attachments/northwind/t1/${name}`,
  };
}

const JSON_FILE = new File(['{"a": 1}'], 'spec.json', { type: 'application/json' });

function storedFile(name = 'spec.json'): Attachment {
  return {
    name,
    size: 8,
    kind: 'file',
    markdown: `[File: ${name}, 8 B]({{MAEL_TASK_DIR}}/images/t1/${name})`,
    url: `/api/attachments/northwind/t1/${name}`,
  };
}

/** The field over a fake API, with the caller's state held as a test double. */
function setup(
  attached: Attachment[] = [],
  props: { disabled?: boolean } = {},
  wrap: (field: React.ReactNode) => React.ReactNode = (field) => field,
) {
  const post = vi.fn().mockResolvedValue({
    markdown: stored().markdown,
    url: stored().url,
    kind: 'image',
  });
  const api = { post } as unknown as ApiClient;
  const onAttach = vi.fn();
  const onRemove = vi.fn();
  render(
    <ApiProvider
      api={api}
      queryClient={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}
    >
      {wrap(
        <AttachField
          project="northwind"
          bucket="t1"
          attached={attached}
          onAttach={onAttach}
          onRemove={onRemove}
          {...props}
        >
          <textarea aria-label="What needs doing?" />
        </AttachField>,
      )}
    </ApiProvider>,
  );
  return { post, onAttach, onRemove };
}

describe('AttachField', () => {
  it('uploads a picked image and hands back the ref to append', async () => {
    const user = userEvent.setup();
    const { post, onAttach } = setup();

    await user.upload(screen.getByLabelText('Attach file', { selector: 'input' }), PNG);

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

    await user.upload(screen.getByLabelText('Attach file', { selector: 'input' }), [PNG, second]);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('shot.png: too large');
    expect(alert).toHaveTextContent('other.png: not an image');
  });

  it('uploads a picked file that is not an image', async () => {
    const user = userEvent.setup();
    const { post, onAttach } = setup();
    post.mockResolvedValue({
      markdown: storedFile().markdown,
      url: storedFile().url,
      kind: 'file',
    });

    await user.upload(screen.getByLabelText('Attach file', { selector: 'input' }), JSON_FILE);

    await waitFor(() => expect(onAttach).toHaveBeenCalledWith(storedFile()));
    const [, body] = post.mock.calls[0] as [string, FormData];
    expect((body.get('file') as File).name).toBe('spec.json');
  });

  it('shows an attached file by its name and size, and offers it for removal', async () => {
    const user = userEvent.setup();
    const { onRemove } = setup([storedFile()]);

    // A file has no thumbnail to draw.
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText('spec.json')).toBeInTheDocument();
    expect(screen.getByText('8 B')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Remove spec.json' }));

    expect(onRemove).toHaveBeenCalledWith(storedFile());
  });

  describe('a text note', () => {
    const open = async (user: ReturnType<typeof userEvent.setup>) => {
      await user.click(screen.getByRole('button', { name: 'Attach text note' }));
      return screen.getByRole('dialog', { name: 'Attach text note' });
    };

    it('uploads the typed text under the typed name, and closes', async () => {
      const user = userEvent.setup();
      const { post, onAttach } = setup();
      post.mockResolvedValue({
        markdown: storedFile('log.txt').markdown,
        url: storedFile('log.txt').url,
        kind: 'file',
      });

      const dialog = await open(user);
      expect(within(dialog).getByLabelText('File name')).toHaveValue('note.txt');
      await user.type(within(dialog).getByLabelText('Content'), 'line one');
      await user.clear(within(dialog).getByLabelText('File name'));
      await user.type(within(dialog).getByLabelText('File name'), 'log');
      await user.click(within(dialog).getByRole('button', { name: 'Attach' }));

      await waitFor(() => expect(onAttach).toHaveBeenCalledTimes(1));
      const [, body] = post.mock.calls[0] as [string, FormData];
      const file = body.get('file') as File;
      // A name with no extension gets `.txt`.
      expect(file.name).toBe('log.txt');
      expect(file.type).toBe('text/plain');
      expect(await file.text()).toBe('line one');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('gives a name with no kept extension `.txt`', async () => {
      const user = userEvent.setup();
      const { post } = setup();

      const dialog = await open(user);
      await user.type(within(dialog).getByLabelText('Content'), 'x');
      await user.clear(within(dialog).getByLabelText('File name'));
      await user.type(within(dialog).getByLabelText('File name'), 'v1.2 notes');
      await user.click(within(dialog).getByRole('button', { name: 'Attach' }));

      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      const [, body] = post.mock.calls[0] as [string, FormData];
      expect((body.get('file') as File).name).toBe('v1.2 notes.txt');
    });

    it('attaches on Enter in the name, and does not submit the form the field is in', async () => {
      const user = userEvent.setup();
      const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
      // A submit button, as New work has: Enter in a form's field presses it.
      const { onAttach } = setup([], {}, (field) => (
        <form onSubmit={onSubmit}>
          {field}
          <button type="submit">Save</button>
        </form>
      ));

      const dialog = await open(user);
      await user.type(within(dialog).getByLabelText('Content'), 'x');
      await user.type(within(dialog).getByLabelText('File name'), '{Enter}');

      await waitFor(() => expect(onAttach).toHaveBeenCalledTimes(1));
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it('closes alone on Escape, not the dialog the field is in', async () => {
      const user = userEvent.setup();
      const onOuterClose = vi.fn();
      setup([], {}, (field) => (
        <Dialog label="New work" onClose={onOuterClose}>
          {field}
        </Dialog>
      ));

      const dialog = await open(user);
      // Escape on a modal dialog arrives as its `cancel` event.
      fireEvent(dialog, new Event('cancel', { cancelable: true }));

      expect(screen.queryByRole('dialog', { name: 'Attach text note' })).not.toBeInTheDocument();
      expect(onOuterClose).not.toHaveBeenCalled();
    });

    it('cannot be attached with no content', async () => {
      const user = userEvent.setup();
      setup();

      const dialog = await open(user);

      expect(within(dialog).getByRole('button', { name: 'Attach' })).toBeDisabled();
    });

    it('stays open and says why when the upload fails', async () => {
      const user = userEvent.setup();
      const { post, onAttach } = setup();
      post.mockRejectedValue(new Error('attachment is too large'));

      const dialog = await open(user);
      await user.type(within(dialog).getByLabelText('Content'), 'x');
      await user.click(within(dialog).getByRole('button', { name: 'Attach' }));

      expect(await within(dialog).findByRole('alert')).toHaveTextContent(
        'note.txt: attachment is too large',
      );
      expect(onAttach).not.toHaveBeenCalled();
    });
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

    await user.upload(screen.getByLabelText('Attach file', { selector: 'input' }), PNG);

    expect(await screen.findByRole('alert')).toHaveTextContent('shot.png: Attachment is too large');
    expect(onAttach).not.toHaveBeenCalled();
  });
});
