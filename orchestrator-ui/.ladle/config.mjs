// Ladle pins the HMR socket to `localhost`. A page on another device needs the dev host.
/** @type {import('@ladle/react').Config} */
export default {
  hmrHost: process.env.DEV_HOST,
  stories: 'src/**/*.stories.{js,jsx,ts,tsx}',
  // The app follows the OS scheme, so a story must be viewable in both without
  // a rebuild. Ladle's own theme control switches the `prefers-color-scheme`
  // the iframe reports.
  addons: { theme: { enabled: true, defaultState: 'dark' }, width: { enabled: true } },
};
