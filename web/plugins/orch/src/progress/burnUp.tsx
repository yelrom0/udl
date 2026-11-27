import type { Forecast, SeriesPoint } from "../model";

/** The chart's own size, in its viewBox's units (it is scaled to the card it sits in). */
export const SIZE = { width: 720, height: 320 } as const;

/** What is left around the plot for its axes: the ticks on the left, the days below. */
const PAD = { left: 40, right: 16, top: 20, bottom: 30 } as const;

/** One point of the plot, in the chart's own units. */
export interface Point {
	readonly x: number;
	readonly y: number;
}

/** A tick of an axis: where it is, and what it says. */
export interface Tick {
	readonly at: number;
	readonly value: number;
}

/** A day on the bottom axis, and what it is called there. */
export interface DayTick {
	readonly at: number;
	readonly label: string;
}

/** Everything `<BurnUp>` draws, worked out once so the drawing is only drawing. */
export interface Plotted {
	readonly width: number;
	readonly height: number;
	/** The points merged by the end of each day of the series, in order. */
	readonly merged: readonly Point[];
	/** The scope as a step line: along a day's scope, then up to the next day's. */
	readonly scope: readonly Point[];
	/** Today's merged points, and the scope's end on the forecast's likely day. */
	readonly forecast: readonly Point[];
	/** The likely range: today's point, then the scope's end on its early and late days. */
	readonly band: readonly Point[];
	/** Today's own place, and the points merged by then (the baseline's included). */
	readonly today: Point;
	/** The points merged by today, baseline and all. */
	readonly todayPoints: number;
	/** The scope's points at its end (the number the forecast aims at). */
	readonly scopeAtEnd: number;
	readonly yTicks: readonly Tick[];
	readonly xTicks: readonly DayTick[];
	/** The top of the y axis: a round number at or above everything drawn. */
	readonly top: number;
	/** Where a day is on the chart. */
	x(day: Date): number;
	/** Where a number of points is on the chart. */
	y(points: number): number;
}

const MONTHS: readonly string[] = [
	"Jan",
	"Feb",
	"Mar",
	"Apr",
	"May",
	"Jun",
	"Jul",
	"Aug",
	"Sep",
	"Oct",
	"Nov",
	"Dec",
];

const WEEKDAYS: readonly string[] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const DAY = 86_400_000;

/** A day, read from `YYYY-MM-DD` or an ISO timestamp, at midnight UTC. */
function dayOf(text: string): Date {
	const day = /^\d{4}-\d{2}-\d{2}$/.test(text) ? new Date(`${text}T00:00:00Z`) : new Date(text);
	return new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), 0, 0, 0, 0));
}

/** A day's date and month, as the axis writes it (`13 Nov`). */
export function dayLabel(day: Date): string {
	return `${day.getUTCDate()} ${MONTHS[day.getUTCMonth()]}`;
}

/** A day with its weekday, as the forecast writes it (`Fri 20 Nov`). */
export function longDayLabel(day: Date): string {
	return `${WEEKDAYS[day.getUTCDay()]} ${dayLabel(day)}`;
}

/**
 * A step that divides a scale into round ticks (`50` for a top of 250, `5` for 20): the first
 * of 1, 2, 2.5, 5 and 10 of its magnitude at or above it. Nothing below one: an axis of no
 * points still has a tick at 1.
 */
export function niceStep(value: number): number {
	const positive = Math.max(value, 1);
	const magnitude = 10 ** Math.floor(Math.log10(positive));
	const ratio = positive / magnitude;
	const step = ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 2.5 ? 2.5 : ratio <= 5 ? 5 : 10;
	return step * magnitude;
}

/**
 * The burn-up's geometry (29.11): the merged line, the scope's steps, the forecast and the band
 * it is likely to land in, from a project's series and its forecast. It answers nothing when
 * there is no day to draw — a project with no sized task has no burn-up yet.
 *
 * The days run from the series' first to the last of its days or the forecast's, so the forecast
 * is on the chart; the two series lines stop at the series' own last day, because past it nothing
 * is known to have merged.
 */
export function plot(
	series: readonly SeriesPoint[],
	forecast: Forecast,
	today: Date,
	size: { readonly width: number; readonly height: number } = SIZE,
	baseline = 0,
): Plotted | null {
	const first = series[0];
	const last = series.at(-1);
	if (first === undefined || last === undefined) {
		return null;
	}
	const start = dayOf(first.day);
	const lastDay = dayOf(last.day);
	const late = forecast.late === "" ? lastDay : dayOf(forecast.late);
	const likely = forecast.likely === "" ? lastDay : dayOf(forecast.likely);
	const end = new Date(Math.max(lastDay.getTime(), late.getTime(), likely.getTime()));
	const span = Math.max(end.getTime() - start.getTime(), DAY);
	const wide = size.width - PAD.left - PAD.right;
	const tall = size.height - PAD.top - PAD.bottom;
	const x = (day: Date): number => PAD.left + ((day.getTime() - start.getTime()) / span) * wide;

	let mergedBy = baseline;
	const merged: Point[] = [];
	let highest = Math.max(baseline, 0);
	for (const one of series) {
		mergedBy += one.merged;
		highest = Math.max(highest, mergedBy, one.points);
	}
	const scopeAtEnd = last.points;
	const step = niceStep(Math.max(highest, 1) / 5);
	const top = Math.max(step, Math.ceil(Math.max(highest, 1) / step) * step);
	const y = (points: number): number => PAD.top + tall - (points / top) * tall;

	mergedBy = baseline;
	for (const one of series) {
		mergedBy += one.merged;
		merged.push({ x: x(dayOf(one.day)), y: y(mergedBy) });
	}

	const scope: Point[] = [];
	series.forEach((one, index) => {
		const next = series[index + 1];
		scope.push({ x: x(dayOf(one.day)), y: y(one.points) });
		if (next !== undefined) {
			scope.push({ x: x(dayOf(next.day)), y: y(one.points) });
		}
	});

	const todayDay = dayOf(today.toISOString());
	const todayX = Math.min(Math.max(x(todayDay), PAD.left), PAD.left + wide);
	const upToToday = series.filter((one) => dayOf(one.day).getTime() <= todayDay.getTime());
	const mergedToday = baseline + upToToday.reduce((total, one) => total + one.merged, 0);
	const todayPoint = { x: todayX, y: y(mergedToday) };

	const earlyDay = forecast.early === "" ? todayDay : dayOf(forecast.early);
	const forecastLine: Point[] = [todayPoint, { x: x(likely), y: y(scopeAtEnd) }];
	const band: Point[] = [
		todayPoint,
		{ x: x(earlyDay), y: y(scopeAtEnd) },
		{ x: x(late), y: y(scopeAtEnd) },
	];

	const yTicks: Tick[] = [];
	for (let value = 0; value <= top; value += step) {
		yTicks.push({ at: y(value), value });
	}

	const days = Math.round((end.getTime() - start.getTime()) / DAY);
	const every = Math.max(1, Math.ceil(days / 8));
	const xTicks: DayTick[] = [];
	for (let index = 0; index <= days; index += every) {
		const day = new Date(start.getTime() + index * DAY);
		const lastOne = index + every > days;
		xTicks.push({
			at: x(day),
			label: index === 0 || lastOne ? dayLabel(day) : `${day.getUTCDate()}`,
		});
	}

	return {
		width: size.width,
		height: size.height,
		merged,
		scope,
		forecast: forecastLine,
		band,
		today: todayPoint,
		todayPoints: mergedToday,
		scopeAtEnd,
		yTicks,
		xTicks,
		top,
		x,
		y,
	};
}

/** A list of points as an SVG `points` attribute. */
function drawn(points: readonly Point[]): string {
	return points.map((one) => `${one.x.toFixed(1)},${one.y.toFixed(1)}`).join(" ");
}

/** What `<BurnUp>` is given: the series, the forecast, and the day that is today. */
export interface BurnUpProps {
	readonly series: readonly SeriesPoint[];
	readonly forecast: Forecast;
	readonly today: Date;
	/** The points merged before the series' first day (what a windowed series cannot hold). */
	readonly baseline?: number;
}

/**
 * The burn-up (29.11): the scope, the points merged, the forecast to its likely day, and the
 * band it is likely to land in — two lines and a band, so no chart library.
 */
export function BurnUp({ series, forecast, today, baseline = 0 }: BurnUpProps) {
	const drawnUp = plot(series, forecast, today, SIZE, baseline);
	if (drawnUp === null) {
		return <p className="text-sm opacity-70">Nothing is sized yet, so there is no burn-up.</p>;
	}
	const likelyDay = forecast.likely === "" ? null : dayOf(forecast.likely);
	const earlyDay = forecast.early === "" ? null : dayOf(forecast.early);
	const lateDay = forecast.late === "" ? null : dayOf(forecast.late);
	const range =
		earlyDay === null || lateDay === null
			? ""
			: earlyDay.getTime() === lateDay.getTime()
				? dayLabel(earlyDay)
				: `${dayLabel(earlyDay)} – ${longDayLabel(lateDay)}`;
	return (
		<figure className="flex flex-col gap-2">
			<svg
				aria-label="The burn-up's lines"
				className="w-full"
				role="img"
				viewBox={`0 0 ${drawnUp.width} ${drawnUp.height}`}
			>
				{drawnUp.yTicks.map((tick) => (
					<g key={tick.value}>
						<line
							className="stroke-base-content/10"
							x1={PAD.left}
							x2={drawnUp.width - PAD.right}
							y1={tick.at}
							y2={tick.at}
						/>
						<text
							className="fill-base-content/60"
							textAnchor="end"
							x={PAD.left - 8}
							y={tick.at + 4}
						>
							{tick.value}
						</text>
					</g>
				))}
				{drawnUp.xTicks.map((tick) => (
					<text
						className="fill-base-content/60"
						key={tick.at}
						textAnchor="middle"
						x={tick.at}
						y={drawnUp.height - 8}
					>
						{tick.label}
					</text>
				))}
				<polygon
					aria-label="likely range"
					className="fill-primary/15"
					points={drawn(drawnUp.band)}
				/>
				<polyline
					aria-label="scope"
					className="fill-none stroke-base-content/60"
					points={drawn(drawnUp.scope)}
					strokeWidth={1.5}
				/>
				<polyline
					aria-label="merged"
					className="fill-none stroke-primary"
					points={drawn(drawnUp.merged)}
					strokeWidth={2}
				/>
				<polyline
					aria-label="forecast"
					className="fill-none stroke-primary"
					points={drawn(drawnUp.forecast)}
					strokeDasharray="6 5"
					strokeWidth={2}
				/>
				<line
					aria-label="today"
					className="stroke-base-content/40"
					strokeDasharray="4 4"
					x1={drawnUp.today.x}
					x2={drawnUp.today.x}
					y1={PAD.top}
					y2={drawnUp.height - PAD.bottom}
				/>
				<circle className="fill-primary" cx={drawnUp.today.x} cy={drawnUp.today.y} r={4} />
				<text
					className="fill-base-content"
					fontWeight={600}
					textAnchor="middle"
					x={drawnUp.today.x}
					y={drawnUp.today.y - 8}
				>
					{drawnUp.todayPoints}
				</text>
				<text
					className="fill-base-content/60"
					textAnchor="end"
					x={drawnUp.width - PAD.right}
					y={drawnUp.y(drawnUp.scopeAtEnd) - 6}
				>
					{`scope ${drawnUp.scopeAtEnd}`}
				</text>
				{likelyDay !== null && (
					<text
						className="fill-base-content"
						fontWeight={600}
						textAnchor="end"
						x={drawnUp.x(likelyDay)}
						y={drawnUp.y(drawnUp.scopeAtEnd) - 22}
					>
						{longDayLabel(likelyDay)}
					</text>
				)}
			</svg>
			<figcaption className="flex flex-wrap items-center gap-4 text-xs opacity-70">
				<span className="flex items-center gap-1">
					<span className="inline-block h-0.5 w-4 bg-primary" /> merged
				</span>
				<span className="flex items-center gap-1">
					<span className="inline-block h-0.5 w-4 bg-base-content/60" /> scope
				</span>
				<span className="flex items-center gap-1">
					<span className="inline-block h-0.5 w-4 border-t-2 border-dashed border-primary" />{" "}
					forecast
				</span>
				<span className="flex items-center gap-1">
					<span className="inline-block h-3 w-4 bg-primary/15" />
					{range === "" ? "likely range" : `likely range (${range})`}
				</span>
			</figcaption>
		</figure>
	);
}
