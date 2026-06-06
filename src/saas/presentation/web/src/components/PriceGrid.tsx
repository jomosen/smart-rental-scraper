import type { CrossTariffResponse } from '../types'
import CategoryBlock from './CategoryBlock'

interface Props {
  data: CrossTariffResponse
  openCats: Set<string>
  onToggleCat: (code: string) => void
  onCellClick: (code: string, duration: number) => void
}

export default function PriceGrid({ data, openCats, onToggleCat, onCellClick }: Props) {
  const { meta, categories } = data
  const colorByKey: Record<string, string> = {}
  for (const p of meta.providers) colorByKey[p.key] = p.color

  if (categories.length === 0) {
    return <p style={{ color: 'var(--ink-faint)' }}>Sin datos para esta zona.</p>
  }

  return (
    <div className="gridwrap">
      <table>
        <tbody>
          {categories.map((cat) => (
            <CategoryBlock
              key={cat.acriss_code}
              category={cat}
              durations={meta.durations}
              colorByKey={colorByKey}
              open={openCats.has(cat.acriss_code)}
              onToggle={onToggleCat}
              onCellClick={onCellClick}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}
