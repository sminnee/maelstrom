/** An id for something the client made: it keys a list, and the server does not read it. */
export const clientId = (prefix: string) =>
  `${prefix}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
