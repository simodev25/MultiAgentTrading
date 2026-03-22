import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { CRYPTO_PAIRS, FOREX_PAIRS } from '../constants/markets';
import type { MarketSymbolsConfig } from '../types';

const FALLBACK_SYMBOLS: MarketSymbolsConfig = {
  forex_pairs: FOREX_PAIRS,
  crypto_pairs: CRYPTO_PAIRS,
  symbol_groups: [
    { name: 'forex', symbols: FOREX_PAIRS },
    { name: 'crypto', symbols: CRYPTO_PAIRS },
  ],
  tradeable_pairs: [...FOREX_PAIRS, ...CRYPTO_PAIRS],
  source: 'fallback',
};

export function useMarketSymbols(token: string | null) {
  const [symbols, setSymbols] = useState<MarketSymbolsConfig>(FALLBACK_SYMBOLS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) {
      setSymbols(FALLBACK_SYMBOLS);
      setError(null);
      return;
    }
    setLoading(true);
    try {
      const payload = await api.getMarketSymbols(token) as MarketSymbolsConfig;
      const forexPairs = Array.isArray(payload.forex_pairs) && payload.forex_pairs.length > 0
        ? payload.forex_pairs
        : FALLBACK_SYMBOLS.forex_pairs;
      const cryptoPairs = Array.isArray(payload.crypto_pairs) && payload.crypto_pairs.length > 0
        ? payload.crypto_pairs
        : FALLBACK_SYMBOLS.crypto_pairs;
      const symbolGroups = Array.isArray(payload.symbol_groups) && payload.symbol_groups.length > 0
        ? payload.symbol_groups
        : FALLBACK_SYMBOLS.symbol_groups;
      const tradeablePairs = Array.isArray(payload.tradeable_pairs) && payload.tradeable_pairs.length > 0
        ? payload.tradeable_pairs
        : Array.from(new Set(symbolGroups.flatMap((group) => group.symbols ?? [])));

      setSymbols({
        forex_pairs: forexPairs,
        crypto_pairs: cryptoPairs,
        symbol_groups: symbolGroups,
        tradeable_pairs: tradeablePairs,
        source: typeof payload.source === 'string' ? payload.source : 'config',
      });
      setError(null);
    } catch (err) {
      setSymbols(FALLBACK_SYMBOLS);
      setError(err instanceof Error ? err.message : 'Cannot load market symbols');
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return {
    symbols,
    instruments: symbols.tradeable_pairs,
    pairs: symbols.tradeable_pairs,
    loading,
    error,
    reload,
  };
}
