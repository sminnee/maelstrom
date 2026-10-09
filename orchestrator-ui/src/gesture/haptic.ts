/**
 * A short haptic tick, where the device has a way to play one.
 *
 * Android Chrome has the Vibration API. iOS Safari has none, but from iOS 18 a
 * toggle of an `<input type="checkbox" switch>` plays the system switch
 * haptic. That is WebKit behaviour, not a standard. Older iOS, and a desktop,
 * get nothing.
 */
export function haptic(): void {
  if (typeof navigator.vibrate === 'function') {
    navigator.vibrate(10);
    return;
  }
  switchLabel()?.click();
}

let label: HTMLLabelElement | null = null;

/** One hidden switch on the body, made on first use. */
function switchLabel(): HTMLLabelElement | null {
  if (label?.isConnected) return label;
  if (typeof document === 'undefined') return null;
  label = document.createElement('label');
  label.setAttribute('aria-hidden', 'true');
  label.style.cssText =
    'position:fixed;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none';
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.setAttribute('switch', '');
  input.tabIndex = -1;
  label.append(input);
  document.body.append(label);
  return label;
}
