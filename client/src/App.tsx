import React, { useEffect } from 'react';
import AppShell from './app/AppShell';
import { usePageActivitySync } from './app/usePageActivitySync';
import ProtectedRoute from './components/auth/ProtectedRoute';
import SvgFilterDefs from './components/SvgFilterDefs';
import { Toaster } from '@/components/ui/sonner';
import { disposeOrb } from './features/home/orb/orb';
import { PillToaster } from './features/home/ui/pillToast';

// `<html data-theme>` and the `.dark` flag are written by ThemeProvider
// (contexts/ThemeContext.tsx) and, before the first paint, by the script in
// index.html. Nothing else may write them.
const App: React.FC = () => {
  // Above the sign-in gate, which draws the orb too: page activity pauses
  // script-driven motion on every screen, and signing out must not free the
  // orb the sign-in screen has just taken. The orb keeps its renderer across
  // screens and goes with the app.
  usePageActivitySync();
  useEffect(() => disposeOrb, []);
  return (
    <>
      <SvgFilterDefs />
      <ProtectedRoute>
        <AppShell />
      </ProtectedRoute>
      <Toaster position="top-right" richColors closeButton />
      {/* Normal mode's bottom-centre pill toasts; see features/home/ui/pillToast. */}
      <PillToaster />
    </>
  );
};

export default App;
