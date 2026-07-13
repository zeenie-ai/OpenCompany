/**
 * Login/Register Page.
 * Shows login form, or register form if registration is available.
 */

import React, { useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';

const LoginPage: React.FC = () => {
  const { login, register, canRegister, error, isLoading } = useAuth();

  const [isRegistering, setIsRegistering] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError(null);

    if (!email || !password) {
      setLocalError('Email and password are required');
      return;
    }

    if (isRegistering) {
      if (!displayName) {
        setLocalError('Display name is required');
        return;
      }
      if (password.length < 8) {
        setLocalError('Password must be at least 8 characters');
        return;
      }
      await register(email, password, displayName);
    } else {
      await login(email, password);
    }
  };

  const toggleMode = () => {
    setIsRegistering(!isRegistering);
    setLocalError(null);
  };

  const displayError = localError || error;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-5">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle className="text-3xl font-bold text-node-agent">OpenCompany</CardTitle>
          <CardDescription>
            {isRegistering ? 'Create your account' : 'Sign in to continue'}
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {displayError && (
            <Alert variant="destructive">
              <AlertDescription>{displayError}</AlertDescription>
            </Alert>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {isRegistering && (
              <div className="space-y-1.5">
                <Label htmlFor="displayName">Display Name</Label>
                <Input
                  id="displayName"
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Your name"
                  disabled={isLoading}
                />
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                disabled={isLoading}
                autoComplete="email"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isRegistering ? 'At least 8 characters' : 'Your password'}
                disabled={isLoading}
                autoComplete={isRegistering ? 'new-password' : 'current-password'}
              />
            </div>

            <Button type="submit" className="w-full" disabled={isLoading}>
              {isLoading
                ? 'Please wait...'
                : isRegistering
                  ? 'Create Account'
                  : 'Sign In'}
            </Button>
          </form>
        </CardContent>

        {canRegister && (
          <CardFooter className="justify-center gap-2 border-t pt-4 text-sm">
            <span className="text-muted-foreground">
              {isRegistering ? 'Already have an account?' : "Don't have an account?"}
            </span>
            <Button
              variant="link"
              onClick={toggleMode}
              disabled={isLoading}
              className="h-auto p-0"
            >
              {isRegistering ? 'Sign In' : 'Register'}
            </Button>
          </CardFooter>
        )}
      </Card>
    </div>
  );
};

export default LoginPage;
