import { useId, useState } from 'react';
import { browserSupportsWebAuthn } from '@simplewebauthn/browser';
import { useRegisterPasskey, useSignIn, type AuthStatus } from '../api/auth';
import { AppButton } from '../ui/AppButton';
import fieldStyles from '../ui/Dialog.module.css';
import styles from './SignIn.module.css';

/** A name for a new passkey, from the browser and the OS it runs on. */
function deviceLabel(userAgent: string): string {
  // Edge and Chrome name Safari too, so the most specific comes first.
  const browser = (
    [
      [/Edg\//, 'Edge'],
      [/Firefox\//, 'Firefox'],
      [/Chrome\//, 'Chrome'],
      [/Safari\//, 'Safari'],
    ] as const
  ).find(([pattern]) => pattern.test(userAgent))?.[1];
  const os = (
    [
      [/iPhone|iPad/, 'iOS'],
      [/Android/, 'Android'],
      [/Mac OS X/, 'macOS'],
      [/Windows/, 'Windows'],
      [/Linux/, 'Linux'],
    ] as const
  ).find(([pattern]) => pattern.test(userAgent))?.[1];
  return [browser, os].filter(Boolean).join(' on ') || 'Passkey';
}

/** The **Sign-in** screen: what the app shows in place of itself until the browser signs in. */
export function SignIn({ status }: { status: AuthStatus }) {
  const register = useRegisterPasskey();
  const signIn = useSignIn();
  return (
    <SignInScreen
      status={status}
      onSignIn={() => signIn.mutateAsync()}
      onCreate={(label) => register.mutateAsync(label)}
    />
  );
}

/** The screen alone, with its actions passed in, so a story draws each state. */
export function SignInScreen({
  status,
  onSignIn,
  onCreate,
}: {
  status: AuthStatus;
  onSignIn: () => Promise<unknown>;
  onCreate: (label: string) => Promise<unknown>;
}) {
  const [label, setLabel] = useState(() => deviceLabel(navigator.userAgent));
  const labelId = useId();
  return (
    <main className={styles.screen} data-testid="sign-in">
      <section className={styles.card}>
        <h1 className={styles.title}>
          <img src="/logo.svg" alt="" className={styles.mark} />
          maelstrom
        </h1>
        {status.state === 'pending' ? (
          <Pending code={status.code} label={status.label} />
        ) : !browserSupportsWebAuthn() ? (
          // A browser offers passkeys only to a secure context.
          <p className={styles.lede} role="alert">
            This browser cannot use a passkey here. Open the app over HTTPS, or at{' '}
            <code>localhost</code> on the host.
          </p>
        ) : (
          <>
            <AppButton variant="primary" onClick={onSignIn} errorChildren={failure}>
              Sign in with a passkey
            </AppButton>
            <div className={styles.rule}>or</div>
            <label className={fieldStyles.field} htmlFor={labelId}>
              <span>Name of this device</span>
              <input id={labelId} value={label} onChange={(e) => setLabel(e.target.value)} />
            </label>
            <AppButton onClick={() => onCreate(label.trim())} errorChildren={failure}>
              Create a passkey
            </AppButton>
          </>
        )}
      </section>
    </main>
  );
}

const failure = (err: unknown) => (err instanceof Error ? err.message : 'Failed');

/** A **Pending passkey**: the command that admits it. The status poll opens the app. */
function Pending({ code, label }: { code: string; label: string }) {
  const command = `mael auth verify ${code}`;
  return (
    <>
      <p className={styles.lede}>
        The passkey <strong>{label}</strong> waits for the host. Run this in a shell on the host:
      </p>
      <div className={styles.command}>
        <code data-testid="verify-command">{command}</code>
        <AppButton variant="quiet" onClick={() => navigator.clipboard.writeText(command)}>
          Copy
        </AppButton>
      </div>
      <p className={styles.faint}>The app opens when the passkey is verified.</p>
    </>
  );
}
