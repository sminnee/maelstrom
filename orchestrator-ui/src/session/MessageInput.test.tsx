import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { ApiProvider } from '../api/ApiProvider';
import type { ApiClient } from '../api/http';
import type { Attachment } from '../api/attachments';
import { MessageInput } from './MessageInput';

describe('the message input', () => {
  const type = (text: string) =>
    fireEvent.change(screen.getByLabelText('Message to agent'), { target: { value: text } });

  /** The input over an API that is never called: these tests type, they do not attach. */
  const renderInput = (props: {
    onSend: (text: string, attachments: Attachment[]) => void | Promise<unknown>;
    onRun: (command: string) => void | Promise<unknown>;
  }) =>
    render(
      <ApiProvider api={{} as ApiClient} queryClient={new QueryClient()}>
        <MessageInput project="northwind" bucket="d9a4c7f1" {...props} />
      </ApiProvider>,
    );

  it('sends what was typed as a message', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const onRun = vi.fn().mockResolvedValue(undefined);
    renderInput({ onSend, onRun });
    type('run the tests');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('run the tests', []);
    expect(onRun).not.toHaveBeenCalled();
  });

  it('runs a `!` line as a shell command instead of saying it', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const onRun = vi.fn().mockResolvedValue(undefined);
    renderInput({ onSend, onRun });
    type('!git status');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    // The prefix is the instruction, so it does not travel with the command.
    expect(onRun).toHaveBeenCalledWith('git status');
    expect(onSend).not.toHaveBeenCalled();
  });

  it('sends nothing for a bare `!`', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const onRun = vi.fn().mockResolvedValue(undefined);
    renderInput({ onSend, onRun });
    type('!   ');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onRun).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
  });
});
