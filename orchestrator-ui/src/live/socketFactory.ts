import { createContext } from 'react';
import { browserSocket, type SocketLike } from './socketLike';

/** How a hook opens a socket. `LiveProvider` sets it, so a test can hand in a fake. */
export const SocketFactoryContext = createContext<(path: string) => SocketLike>(browserSocket);
