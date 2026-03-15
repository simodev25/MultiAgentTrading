import type { MetaApiPosition } from '../../types';
import { resolveTicket } from '../../utils/tradingSymbols';
import { formatPrice, resolveStopLoss, resolveTakeProfit } from '../../utils/priceLevels';
import { displaySymbol } from './formatters';
import { TableSkeletonRows } from './TableSkeletonRows';

interface OpenPositionsTableProps {
  metaLoading: boolean;
  openPositions: MetaApiPosition[];
  selectedChartTicket: string | null;
  onToggleTicket: (ticket: string) => void;
}

export function OpenPositionsTable({
  metaLoading,
  openPositions,
  selectedChartTicket,
  onToggleTicket,
}: OpenPositionsTableProps) {
  return (
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
            return (
              <tr key={`${ticket}-${idx}`}>
                <td>{ticket}</td>
                <td>{String(position.time ?? position.brokerTime ?? '-')}</td>
                <td>{displaySymbol(position.symbol)}</td>
                <td>{String(position.type ?? '-')}</td>
                <td>{typeof position.volume === 'number' ? position.volume.toFixed(2) : '-'}</td>
                <td>{formatPrice(typeof position.openPrice === 'number' ? position.openPrice : null)}</td>
                <td>{formatPrice(typeof position.currentPrice === 'number' ? position.currentPrice : null)}</td>
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
  );
}
