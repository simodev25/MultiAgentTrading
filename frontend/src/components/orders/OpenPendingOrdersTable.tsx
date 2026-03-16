import type { MetaApiOpenOrder } from '../../types';
import { resolveTicket } from '../../utils/tradingSymbols';
import { displaySymbol, formatMetaTradingTime, formatMetaTradingType } from './formatters';
import { TableSkeletonRows } from './TableSkeletonRows';

interface OpenPendingOrdersTableProps {
  metaLoading: boolean;
  openOrders: MetaApiOpenOrder[];
  selectedChartTicket: string | null;
  onToggleTicket: (ticket: string) => void;
}

export function OpenPendingOrdersTable({
  metaLoading,
  openOrders,
  selectedChartTicket,
  onToggleTicket,
}: OpenPendingOrdersTableProps) {
  return (
    <table>
      <thead>
        <tr>
          <th>Ticket</th>
          <th>Time</th>
          <th>Symbol</th>
          <th>Type</th>
          <th>State</th>
          <th>Volume</th>
          <th>Open Price</th>
          <th>Current Price</th>
          <th>Graphique</th>
        </tr>
      </thead>
      <tbody>
        {metaLoading && openOrders.length === 0 ? (
          <TableSkeletonRows prefix="open-orders" columns={9} rows={4} />
        ) : openOrders.length === 0 ? (
          <tr>
            <td colSpan={9}>Aucun ordre en attente sur le compte sélectionné.</td>
          </tr>
        ) : (
          openOrders.map((order, idx) => {
            const ticket = resolveTicket(order as Record<string, unknown>);
            const selected = selectedChartTicket === ticket;
            const selectable = ticket !== '-';
            return (
              <tr key={`${ticket}-${idx}`}>
                <td>{ticket}</td>
                <td>{formatMetaTradingTime(order.time ?? order.brokerTime)}</td>
                <td>{displaySymbol(order.symbol)}</td>
                <td>{formatMetaTradingType(order.type)}</td>
                <td>{formatMetaTradingType(order.state)}</td>
                <td>{typeof order.volume === 'number' ? order.volume.toFixed(2) : (typeof order.currentVolume === 'number' ? order.currentVolume.toFixed(2) : '-')}</td>
                <td>{typeof order.openPrice === 'number' ? order.openPrice.toFixed(5) : '-'}</td>
                <td>{typeof order.currentPrice === 'number' ? order.currentPrice.toFixed(5) : '-'}</td>
                <td>
                  <button
                    type="button"
                    disabled={!selectable}
                    aria-label={`Afficher ticket ${ticket} sur le graphique depuis ordres en attente`}
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
