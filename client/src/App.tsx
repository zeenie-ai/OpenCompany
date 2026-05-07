import React, { useEffect } from 'react';
import Dashboard from './Dashboard';
import ProtectedRoute from './components/auth/ProtectedRoute';
import SvgFilterDefs from './components/SvgFilterDefs';
import { useTheme } from './contexts/ThemeContext';
import { Toaster } from '@/components/ui/sonner';

const App: React.FC = () => {
  const { isDarkMode } = useTheme();

  // Sync the theme flag onto <html> so Tailwind `dark:` variants and the
  // [data-theme=dark] tokens in tokens.css both resolve correctly. Replaces
  // the antd ConfigProvider algorithm switch.
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', isDarkMode);
    root.dataset.theme = isDarkMode ? 'dark' : 'light';
  }, [isDarkMode]);

  return (
    <>
      <SvgFilterDefs />
      <ProtectedRoute>
        <div className="flex h-screen w-screen">
          <Dashboard />
        </div>
      </ProtectedRoute>
      <Toaster position="top-right" richColors closeButton />
    </>
  );
};

export default App;
