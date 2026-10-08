/**
 * The sign-in gate: what shows before the app (onboarding handoff R4).
 *
 * | State                                   | Shows                                          |
 * |-----------------------------------------|------------------------------------------------|
 * | checking the session                    | a loading screen                               |
 * | the server can't be reached             | Connecting: a countdown to each check, Try now |
 * | the server answered                     | "Connected", for CONNECT_RETRY.CONNECTED_HOLD_MS |
 * | then                                    | sign-in, or the app (a good session, or login off) |
 * | signed in, the WebSocket down           | Connecting over the app                        |
 *
 * Connecting and sign-in render in one ConnectScreen, so the orb glides
 * between their slots. Over the app, the app stays mounted and inert, and
 * the checks are HTTP session checks, so a session that expired while the
 * server was away goes to sign-in; the overlay goes once the WebSocket is
 * back. The auth check never retries on its own (AuthContext).
 */

import React from 'react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { useWebSocketActions } from '../../contexts/WebSocketContext';
import { CONNECT_RETRY } from '../../lib/connectionConfig';
import { ConnectingPanel } from './ConnectingPanel';
import { ConnectScreen } from './ConnectScreen';
import LoginPage from './LoginPage';
import { useHold } from './useHold';

function ScreenLoading() {
  return (
    <div className="flex h-screen items-center justify-center bg-background text-foreground">
      <div className="text-center">
        <Loader2 className="mx-auto mb-4 h-10 w-10 animate-spin text-node-agent" />
        <p>Loading...</p>
      </div>
    </div>
  );
}

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isLoading, error, checkAuth } = useAuth();
  const { reconnecting } = useWebSocketActions();
  const unreachable = error !== null && !isAuthenticated;
  const connecting = useHold(unreachable, CONNECT_RETRY.CONNECTED_HOLD_MS);
  const away = isAuthenticated && reconnecting;
  const overlay = useHold(away, CONNECT_RETRY.CONNECTED_HOLD_MS);

  if (isLoading) return <ScreenLoading />;

  if (connecting || !isAuthenticated) {
    return (
      <ConnectScreen>
        {connecting ? <ConnectingPanel check={checkAuth} connected={!unreachable} /> : <LoginPage />}
      </ConnectScreen>
    );
  }

  return (
    <>
      <div className="contents" inert={overlay}>
        {children}
      </div>
      {overlay && (
        <ConnectScreen overlay>
          <ConnectingPanel check={checkAuth} connected={!away} again />
        </ConnectScreen>
      )}
    </>
  );
};

export default ProtectedRoute;
