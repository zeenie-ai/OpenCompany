import { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RotateCcw, RefreshCw, Bug } from 'lucide-react';
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
  errorInfo?: ErrorInfo;
}

class ErrorBoundary extends Component<Props, State> {
  public state: State = { hasError: false };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    this.setState({ error, errorInfo });
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: undefined, errorInfo: undefined });
  };

  private handleReload = () => {
    window.location.reload();
  };

  public render() {
    if (!this.state.hasError) return this.props.children;
    if (this.props.fallback) return this.props.fallback;

    return (
      <div className="flex h-full w-full items-center justify-center bg-background p-8">
        <Card className="w-full max-w-xl">
          <CardContent className="space-y-4 p-6 text-center">
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Canvas Error Detected</AlertTitle>
              <AlertDescription>
                The workflow canvas encountered an error and needs to be reset.
                Your workflow data is safe and will be restored.
              </AlertDescription>
            </Alert>

            <div className="flex flex-wrap justify-center gap-3">
              <Button onClick={this.handleReset}>
                <RotateCcw className="h-4 w-4" />
                Reset Canvas
              </Button>
              <Button variant="outline" onClick={this.handleReload}>
                <RefreshCw className="h-4 w-4" />
                Reload Page
              </Button>
            </div>

            {import.meta.env.DEV && this.state.error && (
              <details className="rounded-sm border border-border bg-muted p-3 text-left">
                <summary className="mb-2 flex cursor-pointer items-center gap-2 font-medium text-foreground">
                  <Bug className="h-3.5 w-3.5" />
                  Debug Information
                </summary>
                <pre className="m-0 max-h-[200px] overflow-y-auto font-mono text-xs whitespace-pre-wrap break-words text-muted-foreground">
                  {this.state.error.toString()}
                  {this.state.errorInfo && this.state.errorInfo.componentStack}
                </pre>
              </details>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }
}

export default ErrorBoundary;
