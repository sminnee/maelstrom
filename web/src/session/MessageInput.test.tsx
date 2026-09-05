import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageInput } from './MessageInput';

describe('the message input', () => {
  const type = (text: string) =>
    fireEvent.change(screen.getByLabelText('Message to agent'), { target: { value: text } });

  it('sends what was typed as a message', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const onRun = vi.fn().mockResolvedValue(undefined);
    render(<MessageInput onSend={onSend} onRun={onRun} />);
    type('run the tests');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('run the tests');
    expect(onRun).not.toHaveBeenCalled();
  });

  it('runs a `!` line as a shell command instead of saying it', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const onRun = vi.fn().mockResolvedValue(undefined);
    render(<MessageInput onSend={onSend} onRun={onRun} />);
    type('!git status');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    // The prefix is the instruction, so it does not travel with the command.
    expect(onRun).toHaveBeenCalledWith('git status');
    expect(onSend).not.toHaveBeenCalled();
  });

  it('sends nothing for a bare `!`', async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const onRun = vi.fn().mockResolvedValue(undefined);
    render(<MessageInput onSend={onSend} onRun={onRun} />);
    type('!   ');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onRun).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
  });
});
