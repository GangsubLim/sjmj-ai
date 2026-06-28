import * as React from "react";
import { format } from "date-fns";

import type { Invoice } from "@/types/invoice";
import type { Issuer } from "@/types/settings";
import { formatNumber, numberToKorean } from "@/utils/calculations";
import { formatBusinessNumber } from "@/utils/formatters";

interface InvoiceDocumentProps {
  invoice: Invoice;
  issuer: Issuer;
}

const InvoiceDocument = React.forwardRef<HTMLDivElement, InvoiceDocumentProps>(
  ({ invoice, issuer }, ref) => {
    const telFax =
      issuer.phone && issuer.fax
        ? `${issuer.phone}/${issuer.fax}`
        : issuer.tel_fax || "";

    const stampSrc = issuer.stamp_image_url || "/images/sjdojang.png";

    return (
      <div ref={ref} className="invoice-document">
        {/* 문서 제목 */}
        <h1 className="document-title">{invoice.document_title}</h1>

        {/* 통합 테이블 래퍼 */}
        <div className="invoice-tables-wrapper">
          {/* 상단 정보 섹션 */}
          <div className="top-info-section">
            {/* 발행일 */}
            <div className="issue-date-row">
              <span className="label">발행일:</span>
              <span className="value">
                {format(new Date(invoice.issue_date), "yyyy년 M월 d일")}
              </span>
            </div>

            {/* 좌우 정보 테이블 - Grid 시스템 */}
            <div
              className={`info-table-grid ${!invoice.vehicle_no || invoice.vehicle_no.trim() === "" ? "no-vehicle" : ""}`}
            >
              {/* 수신처 라벨 */}
              <div className="grid-recipient-label">
                <div className="vertical-text">
                  <span>수</span>
                  <br />
                  <span>신</span>
                  <br />
                  <span>처</span>
                </div>
              </div>

              {/* 수신처 내용 */}
              <div className="grid-recipient-content">
                <div className="recipient-name-section">
                  <div className="recipient-lines">
                    <span className="recipient-name">
                      {invoice.recipient || "수신처"}
                    </span>
                    {invoice.recipient2 && (
                      <span className="recipient-name recipient-name-2">
                        {invoice.recipient2}
                      </span>
                    )}
                  </div>
                  <span className="recipient-suffix">귀하</span>
                </div>
              </div>

              {/* 차량번호 영역 */}
              {invoice.vehicle_no && invoice.vehicle_no.trim() !== "" && (
                <div className="grid-vehicle-section">
                  <span className="vehicle-label">차량번호 :</span>
                  <span className="vehicle-value">{invoice.vehicle_no}</span>
                </div>
              )}

              {/* 사업자 정보 */}
              <div className="grid-label-business-number">사업자번호</div>
              <div className="grid-value-business-number">
                {formatBusinessNumber(issuer.business_number)}
              </div>

              <div className="grid-label-company-name">상 호</div>
              <div className="grid-value-company-name">
                {issuer.company_name}
              </div>

              <div className="grid-label-representative">대 표 자</div>
              <div className="grid-value-representative">
                <span>{issuer.representative}</span>
                <span className="stamp-area">(인)</span>
                {invoice.show_stamp && (
                  <img
                    src={stampSrc}
                    alt="도장"
                    className="stamp-image"
                    width={120}
                    height={60}
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                    }}
                  />
                )}
              </div>

              <div className="grid-label-address">주 소</div>
              <div className="grid-value-address">{issuer.address}</div>

              <div className="grid-label-business-type">업태/종목</div>
              <div className="grid-value-business-type">
                {issuer.business_type}/{issuer.business_item}
              </div>

              <div className="grid-label-bank-account">입금계좌</div>
              <div className="grid-value-bank-account">
                {issuer.bank_account}
              </div>

              <div className="grid-label-tel-fax">TEL/FAX</div>
              <div className="grid-value-tel-fax">{telFax}</div>
            </div>
          </div>

          {/* 합계금액 박스 */}
          <div className="total-amount-box">
            <div className="total-label-cell">합계금액</div>
            <div className="total-value-cell">
              <span className="korean-amount">
                {numberToKorean(invoice.grand_total).replace("원정", "")}
                &nbsp;&nbsp;원정
              </span>
              <span className="numeric-amount">
                (&nbsp;&nbsp;₩&nbsp;{formatNumber(invoice.grand_total)}
                &nbsp;&nbsp;)
              </span>
              <span className="vat-included">&nbsp;&nbsp;(부가세포함)</span>
            </div>
          </div>

          {/* 품목 테이블 */}
          <div className="items-grid-container">
            <div className="items-grid-header">
              <div>품목</div>
              <div>수량</div>
              <div>단가</div>
              <div>공급가액</div>
              <div>세액</div>
              <div>합계</div>
            </div>

            <div className="items-grid-body">
              {invoice.items
                .filter((item) => item.name.trim() !== "")
                .map((item, index) => (
                  <React.Fragment key={index}>
                    <div
                      className={`grid-item-name${item.deduction ? "grid-item-deduction" : ""}`}
                    >
                      {item.name}
                    </div>
                    <div>{item.quantity}</div>
                    <div>{formatNumber(Number(item.unit_price))}</div>
                    <div>{formatNumber(item.supply)}</div>
                    <div>{formatNumber(item.vat)}</div>
                    <div>
                      <strong>{formatNumber(item.total)}</strong>
                    </div>
                  </React.Fragment>
                ))}
            </div>

            <div className="items-grid-footer">
              <div className="grid-sum-label">합계</div>
              <div>
                <strong>{formatNumber(invoice.total_supply)}</strong>
              </div>
              <div>
                <strong>{formatNumber(invoice.total_vat)}</strong>
              </div>
              <div>
                <strong>{formatNumber(invoice.grand_total)}</strong>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  },
);

InvoiceDocument.displayName = "InvoiceDocument";

export { InvoiceDocument };
