import type { MetaApiDeal } from '../../types';
import { resolveTicket } from '../../utils/tradingSymbols';
import { displaySymbol } from './formatters';
import { TableSkeletonRows } from './TableSkeletonRows';

interface DealsTableProps {
  metaLoading: boolean;
  deals: MetaApiDeal[];
  pagedDeals: MetaApiDeal[];
  dealsPage: number;
  dealsTotalPages: number;
  dealsPerPage: number;
  onPreviousPage: () => void;
  onNextPage: () => void;
}

export function DealsTable({
  metaLoading,
  deals,
  pagedDeals,
  dealsPage,
  dealsTotalPages,
  dealsPerPage,
  onPreviousPage,
  onNextPage,
}: DealsTableProps) {
  return (
    <>
      <table>
        <thead>
          <tr>
            <th>Ticket</th>
            <th>Time</th>
            <th>Symbol</th>
            <th>Type</th>
            <th>Volume</th>
            <th>Price</th>
            <th>PnL</th>
          </tr>
        </thead>
        <tbody>
          {metaLoading && deals.length === 0 ? (
            <TableSkeletonRows prefix="deals" columns={7} rows={6} />
          ) : deals.length === 0 ? (
            <tr>
              <td colSpan={7}>Aucun deal remonté sur la fenêtre sélectionnée.</td>
            </tr>
          ) : (
            pagedDeals.map((deal, idx) => (
              <tr key={`${resolveTicket(deal as Record<string, unknown>)}-${idx}`}>
                <td>{resolveTicket(deal as Record<string, unknown>)}</td>
                <td>{String(deal.time ?? deal.brokerTime ?? '-')}</td>
                <td>{displaySymbol(deal.symbol)}</td>
                <td>{String(deal.type ?? deal.entryType ?? '-')}</td>
                <td>{typeof deal.volume === 'number' ? deal.volume.toFixed(2) : '-'}</td>
                <td>{typeof deal.price === 'number' ? deal.price.toFixed(5) : '-'}</td>
                <td>{typeof deal.profit === 'number' ? deal.profit.toFixed(2) : '-'}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      {deals.length > 0 && (
        <div className="table-pagination">
          <p className="table-pagination-meta">
            {(dealsPage - 1) * dealsPerPage + 1}-{Math.min(deals.length, dealsPage * dealsPerPage)} sur {deals.length}
          </p>
          <div className="table-pagination-actions">
            <button className="btn-ghost btn-small" type="button" disabled={dealsPage <= 1} onClick={onPreviousPage}>
              Précédent
            </button>
            <span>
              Page {dealsPage} / {dealsTotalPages} ({dealsPerPage} par page)
            </span>
            <button className="btn-ghost btn-small" type="button" disabled={dealsPage >= dealsTotalPages} onClick={onNextPage}>
              Suivant
            </button>
          </div>
        </div>
      )}
    </>
  );
}
