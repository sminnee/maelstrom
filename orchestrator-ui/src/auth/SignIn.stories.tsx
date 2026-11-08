import type { Story } from '@ladle/react';
import { SignInScreen } from './SignIn';

export default { title: 'Auth / Sign-in' };

const never = () => new Promise<never>(() => {});
const refused = () => Promise.reject(new Error('The passkey was not accepted'));

/** No sign-in: the two ways in. */
export const Anonymous: Story = () => (
  <SignInScreen status={{ state: 'anonymous' }} onSignIn={never} onCreate={never} />
);

/** A new passkey that waits for `mael auth verify` on the host. */
export const Pending: Story = () => (
  <SignInScreen
    status={{ state: 'pending', code: 'K7QF-2M9D', label: 'Chrome on macOS' }}
    onSignIn={never}
    onCreate={never}
  />
);

/** A ceremony the server refused: the button says why. Click either one. */
export const Refused: Story = () => (
  <SignInScreen status={{ state: 'anonymous' }} onSignIn={refused} onCreate={refused} />
);
