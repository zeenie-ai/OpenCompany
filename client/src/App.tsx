import React from 'react';
import AppShell from './app/AppShell';
import ProtectedRoute from './components/auth/ProtectedRoute';
import SvgFilterDefs from './components/SvgFilterDefs';
import { Toaster } from '@/components/ui/sonner';
import { PillToaster } from './features/home/ui/pillToast';

// `<html data-theme>` and the `.dark` flag are written by ThemeProvider
// (contexts/ThemeContext.tsx) and, before the first paint, by the script in
// index.html. Nothing else may write them.
const App: React.FC = () => (
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

export default App;
