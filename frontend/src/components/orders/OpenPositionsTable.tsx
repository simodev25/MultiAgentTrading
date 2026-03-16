import { useMemo } from 'react';
import type { MetaApiPosition } from '../../types';
import { resolveTicket } from '../../utils/tradingSymbols';
import { formatPrice, resolveStopLoss, resolveTakeProfit } from '../../utils/priceLevels';
import { displaySymbol, formatMetaTradingTime, formatMetaTradingType } from './formatters';
import { TableSkeletonRows } from './TableSkeletonRows';

interface OpenPositionsTableProps {
  metaLoading: boolean;
  openPositions: MetaApiPosition[];
  selectedChartTicket: string | null;
  onToggleTicket: (ticket: string) => void;
}

type PositionTotals = {
  count: number;
  volume: number;
  pnl: number;
};

function toNullableNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return null;
}

function resolveLivePrice(snapshot: Record<string, unknown>): number | null {
  const direct =
    toNullableNumber(snapshot.currentPrice)
    ?? toNullableNumber(snapshot.currentTickValue)
    ?? toNullableNumber(snapshot.lastPrice)
    ?? toNullableNumber(snapshot.marketPrice)
    ?? toNullableNumber(snapshot.price);
  if (direct != null) return direct;

  const bid = toNullableNumber(snapshot.bid);
  const ask = toNullableNumber(snapshot.ask);
  if (bid != null && ask != null) return (bid + ask) / 2;

  return toNullableNumber(snapshot.openPrice);
}

function formatSigned(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}`;
}

export function OpenPositionsTable({
  metaLoading,
  openPositions,
  selectedChartTicket,
  onToggleTicket,
}: OpenPositionsTableProps) {
  const totals = useMemo<PositionTotals>(() => {
    return openPositions.reduce<PositionTotals>(
      (acc, position) => {
        const volume = typeof position.volume === 'number' && Number.isFinite(position.volume) ? position.volume : 0;
        const pnl = typeof position.profit === 'number' && Number.isFinite(position.profit) ? position.profit : 0;
        return {
          count: acc.count + 1,
          volume: acc.volume + volume,
          pnl: acc.pnl + pnl,
        };
      },
      { count: 0, volume: 0, pnl: 0 },
    );
  }, [openPositions]);

  return (
    <>
      {openPositions.length > 0 && (
        <p className="model-source">
          Total: <code>{totals.count}</code> positions | Volume: <code>{totals.volume.toFixed(2)}</code> | PnL:{' '}
          <strong className={totals.pnl >= 0 ? 'ok-text' : 'danger-text'}>{formatSigned(totals.pnl)}</strong>
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Ticket</th>
            <th>Time</th>
            <th>Symbol</th>
            <th>Type</th>
            <th>Volume</th>
            <th>Open Price</th>
            <th>Current Price</th>
            <th>S/L</th>
            <th>T/P</th>
            <th>PnL</th>
            <th>Graphique</th>
          </tr>
        </thead>
        <tbody>
          {metaLoading && openPositions.length === 0 ? (
            <TableSkeletonRows prefix="positions" columns={11} rows={4} />
          ) : openPositions.length === 0 ? (
            <tr>
              <td colSpan={11}>Aucun ordre ouvert sur le compte sélectionné.</td>
            </tr>
          ) : (
            openPositions.map((position, idx) => {
              const ticket = resolveTicket(position as Record<string, unknown>);
              const selected = selectedChartTicket === ticket;
              const selectable = ticket !== '-';
              const stopLoss = resolveStopLoss(position as Record<string, unknown>);
              const takeProfit = resolveTakeProfit(position as Record<string, unknown>);
              const current = resolveLivePrice(position as Record<string, unknown>);
              return (
                <tr key={`${ticket}-${idx}`}>
                  <td>{ticket}</td>
                  <td>{formatMetaTradingTime(position.time ?? position.brokerTime)}</td>
                  <td>{displaySymbol(position.symbol)}</td>
                  <td>{formatMetaTradingType(position.type)}</td>
                  <td>{typeof position.volume === 'number' ? position.volume.toFixed(2) : '-'}</td>
                  <td>{formatPrice(typeof position.openPrice === 'number' ? position.openPrice : null)}</td>
                  <td>{formatPrice(current)}</td>
                  <td>{formatPrice(stopLoss)}</td>
                  <td>{formatPrice(takeProfit)}</td>
                  <td>{typeof position.profit === 'number' ? position.profit.toFixed(2) : '-'}</td>
                  <td>
                    <button
                      type="button"
                      disabled={!selectable}
                      aria-label={`Afficher ticket ${ticket} sur le graphique`}
                      onClick={() => {
                        if (!selectable) return;
                        onToggleTicket(ticket);
                      }}
                    >
                      {selected ? 'Masquer' : 'Afficher'}
                    </button>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </>
  );
}
