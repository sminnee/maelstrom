// The module the plugin injects into each page. The dev server always sets
// `import.meta.hot`; the guard is for its type.
import { mountJig } from './overlay';

if (import.meta.hot) void mountJig({ hot: import.meta.hot });
