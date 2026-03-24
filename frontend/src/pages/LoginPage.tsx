import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { Cpu, Lock } from 'lucide-react';

export function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();

  const [email, setEmail] = useState('admin@local.dev');
  const [password, setPassword] = useState('admin1234');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await login(email, password);
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-bg flex items-center justify-center p-4">
      <div className="hw-surface w-full max-w-sm p-8">
        {/* Brand */}
        <div className="flex flex-col items-center gap-4 mb-8">
          <div className="w-12 h-12 rounded-xl bg-accent/15 border border-accent/25 flex items-center justify-center">
            <Cpu className="w-6 h-6 text-accent" />
          </div>
          <div className="text-center">
            <span className="text-[13px] font-bold tracking-[0.14em] text-accent uppercase block">
              AGENT_TERMINAL
            </span>
            <span className="text-[9px] text-text-dim tracking-[0.14em] uppercase block mt-1">
              AUTHENTICATION_REQUIRED
            </span>
          </div>
        </div>

        {/* Form */}
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div>
            <label className="micro-label block mb-2">EMAIL_ID</label>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              required
            />
          </div>
          <div>
            <label className="micro-label block mb-2">ACCESS_KEY</label>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              required
            />
          </div>
          {error && <div className="alert">{error}</div>}
          <button className="btn-primary w-full mt-2" disabled={loading}>
            <Lock className="w-3.5 h-3.5" />
            {loading ? 'CONNECTING...' : 'INITIALIZE_SESSION'}
          </button>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-center gap-2 mt-6">
          <div className="led led-blue" />
          <span className="text-[8px] text-text-dim tracking-[0.14em] uppercase">
            SECURE_LINK // v4.2
          </span>
        </div>
      </div>
    </div>
  );
}
