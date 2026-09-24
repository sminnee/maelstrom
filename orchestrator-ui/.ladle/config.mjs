/** @type {import('@ladle/react').Config} */
export default {
  stories: 'src/**/*.stories.{js,jsx,ts,tsx}',
  // The app follows the OS scheme, so a story must be viewable in both without
  // a rebuild. Ladle's own theme control switches the `prefers-color-scheme`
  // the iframe reports.
  addons: { theme: { enabled: true, defaultState: 'dark' }, width: { enabled: true } },
};
