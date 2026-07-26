export type TaskStatus =
  | "PENDING"
  | "RUNNING"
  | "WAITING"
  | "WAITING_CONFIRMATION"
  | "SUCCESS"
  | "FAILED"
  | "CANCELLED"
  | "UNKNOWN";

export type OperationStatus =
  | "STARTED"
  | "SUCCESS"
  | "FAILED"
  | "RETRYING"
  | "SKIPPED"
  | "UNKNOWN";

export interface OperationContext {
  taskId: string;
  stepId: string;
}

export interface AgentErrorPayload {
  code: string;
  message: string;
  userMessage: string;
  retryable: boolean;
}

export interface ToolResponse<T> {
  success: boolean;
  status: TaskStatus;
  data: T | null;
  error: AgentErrorPayload | null;
}

export interface LoginState {
  loggedIn: boolean;
  reason: "AUTHENTICATED_HEADER" | "LOGIN_CONTROL_VISIBLE" | "LOGIN_URL";
  currentUrl: string;
  userMessage: string | null;
}

export type NavigationStepName =
  | "open_selection"
  | "open_product_module"
  | "open_product_search"
  | "open_category_menu"
  | "select_sports_outdoor"
  | "select_sports_apparel";

export interface NavigationCheck {
  step: NavigationStepName;
  status: "SUCCESS";
  currentUrl: string;
  evidence: Record<string, boolean | number | string | null>;
}

export interface ProductRecord {
  title: string;
  productUrl: string | null;
  imageUrl: string | null;
  price: string | null;
  stockStatus: string | null;
  category: string | null;
  commissionRate: string | null;
  rating: string | null;
  shopName: string | null;
  shopUrl: string | null;
  shopTotalSales: string | null;
  topVideoImages: string[];
  recent7DaySales: string | null;
  totalSales: string | null;
  recent7DayRevenue: string | null;
  totalRevenue: string | null;
  relatedCreators: string | null;
  creatorOrderRate: string | null;
  columns: Record<string, string>;
}

export interface ProductExtractionResult {
  source: "chuhaijiang";
  sourceUrl: string;
  country: string | null;
  selectedCategory: string | null;
  visibleRowCount: number;
  returnedRowCount: number;
  scannedPageCount: number;
  extractedAt: string;
  products: ProductRecord[];
}

export interface CreatorTaskProductRecord {
  title: string;
  productUrl: string;
  totalSales: string | null;
  recent7DayRevenue: string | null;
  totalRevenue: string | null;
  relatedCreators: string | null;
}

export interface CreatorTaskProductCollection {
  sourceUrl: string;
  country: string | null;
  selectedCategory: string | null;
  returnedRowCount: number;
  scannedPageCount: number;
  products: CreatorTaskProductRecord[];
}

export interface ProductDetailCheck {
  productId: string;
  productUrl: string;
  title: string;
  openedInNewTab: boolean;
  currentUrl: string;
}

export interface RelatedCreatorsCheck {
  productId: string;
  currentUrl: string;
  creatorCount: number | null;
  tableVisible: boolean;
  tableHeaders: string[];
}

export interface CreatorExportResult {
  productId: string;
  requestedRowCount: 100;
  exportedRowCount: number;
  fileName: string;
  filePath: string;
  fileSizeBytes: number;
  downloadedAt: string;
}

export interface OperationLogRecord {
  taskId: string;
  stepId: string;
  sequenceNumber: number;
  module: "CHROME_MCP" | "CHROME_CDP";
  operation: string;
  operationType: string;
  status: OperationStatus;
  message: string;
  inputSummary: unknown;
  outputSummary: unknown;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
  createdAt: string;
}
