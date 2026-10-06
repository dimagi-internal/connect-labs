/**
 * LabsReport -- the shared component library for indicator reports.
 *
 * Published on `window.LabsReport` by the workflow runner, so ANY render code
 * can use it at runtime: a template-following report, a forked copy, or one
 * written live through MCP. Render code is transpiled in the browser and handed
 * the runner's own React, which is the React these components are built
 * against -- one instance, so hooks work across the boundary.
 *
 *   var R = window.LabsReport;
 *   <R.HeadlineTiles tiles={...} />
 *
 * THE CONTRACT. Components take plain data and callbacks and never fetch: the
 * render owns its data. Once shipped, a prop is never renamed or repurposed --
 * a render written against today's library must keep working after a deploy.
 * A breaking change is a new name. `VERSION` rises with every addition, so a
 * render can check for what it needs.
 */
import * as format from './format';
import * as sort from './sort';
import { HeadlineTiles, tileDelta, tileValue } from './HeadlineTiles';
import { TrendCard, WeeklyActivity, WeeklyActivityCard } from './Charts';
import {
  AttentionCell,
  ScoreCell,
  ScoreCellText,
  ScorecardLegend,
  scoreSortValue,
} from './Scorecard';
import {
  PeerBars,
  PeerCard,
  PeerTrend,
  periodLabel,
  periodNumber,
} from './Peers';
import {
  MiniRankBars,
  RankedBars,
  Tabs,
  drawable,
  ordinal,
  rankOrganisations,
  targetOf,
} from './Benchmark';
import {
  DefinitionBody,
  DefinitionModal,
  ReadingChart,
  cap,
  allCasesDone,
  caseDone,
  caseLabel,
  displayOf,
  fmtCaseField,
  headlineSpecs,
  nounCount,
  scorecardLayout,
  targetValue,
  visitFlagsOf,
} from './Indicators';
import {
  Field,
  NumberField,
  RangeField,
  RangeStrip,
  Segmented,
  StatTiles,
  rangeText,
  stripPos,
} from './Scenario';
import {
  EnrolmentDailyChart,
  EnrolmentTargetChart,
  EnrolmentTargetLegend,
  dailyProgress,
  dailyReadout,
  EnrolmentTargetSummary,
  elapsedFraction,
  enrolmentProgress,
  monthLbl,
  targetWindow,
  targetedLlos,
} from './Targets';
import {
  Button,
  Card,
  Loading,
  Notice,
  Pill,
  ReportHeader,
  SectionTitle,
} from './Layout';

export const VERSION = 9;

export const LabsReport = {
  VERSION,
  // formatting and bands
  nCount: format.nCount,
  dateLbl: format.dateLbl,
  daysBetween: format.daysBetween,
  fmtValue: format.fmtValue,
  hasValue: format.hasValue,
  tintFor: format.tintFor,
  bandColour: format.bandColour,
  attention: format.attention,
  BAND_CLS: format.BAND_CLS,
  BAND_WORD: format.BAND_WORD,
  BAND_TEXT: format.BAND_TEXT,
  // sorting
  sortRows: sort.sortRows,
  nextSort: sort.nextSort,
  useTableSort: sort.useTableSort,
  // page chrome
  Card,
  SectionTitle,
  Notice,
  Loading,
  Pill,
  Button,
  ReportHeader,
  // headline
  HeadlineTiles,
  tileValue,
  tileDelta,
  // charts
  WeeklyActivity,
  WeeklyActivityCard,
  TrendCard,
  // scorecard
  ScoreCell,
  ScoreCellText,
  AttentionCell,
  ScorecardLegend,
  scoreSortValue,
  // peers
  PeerBars,
  PeerTrend,
  PeerCard,
  periodNumber,
  periodLabel,
  // organisation benchmark scorecard (VERSION 2)
  Tabs,
  MiniRankBars,
  RankedBars,
  rankOrganisations,
  targetOf,
  drawable,
  ordinal,
  // the registry display contract, for generic indicator reports (VERSION 3)
  displayOf,
  headlineSpecs,
  scorecardLayout,
  targetValue,
  fmtCaseField,
  nounCount,
  cap,
  ReadingChart,
  DefinitionBody,
  DefinitionModal,
  // scenario reports: inputs the reader changes, read-outs of modelled ranges (VERSION 4)
  StatTiles,
  NumberField,
  RangeField,
  Field,
  Segmented,
  RangeStrip,
  rangeText,
  stripPos,
  // enrolment against a configured target (VERSION 5)
  enrolmentProgress,
  elapsedFraction,
  targetedLlos,
  targetWindow,
  monthLbl,
  EnrolmentTargetChart,
  EnrolmentTargetSummary,
  EnrolmentTargetLegend,
  // this month, day by day (VERSION 6)
  dailyProgress,
  dailyReadout,
  EnrolmentDailyChart,
  // per-visit review flags from the display contract (VERSION 7)
  visitFlagsOf,
  // a case's human label from the display contract (VERSION 8)
  caseLabel,
  // whether a case's work is finished, so no visit is due (VERSION 9)
  caseDone,
  allCasesDone,
};

export type LabsReportLibrary = typeof LabsReport;
