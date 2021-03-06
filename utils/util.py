import torch
from torch import nn
import numpy as np

B = 2
NB_CLASSES = 10


def xywhc2label(bboxs):
    # bboxs is a xywhc list: [(x,y,w,h,c),(x,y,w,h,c)....]
    label = np.zeros((7, 7, 5*B+NB_CLASSES))
    for x, y, w, h, c in bboxs:
        x_grid = int(x//(1.0/7))
        y_grid = int(y//(1.0/7))
        xx = x/(1.0/7)-x_grid
        yy = y/(1.0/7)-y_grid
        label[x_grid, y_grid, 0:5] = np.array([xx, yy, w, h, 1])
        label[x_grid, y_grid, 5:10] = np.array([xx, yy, w, h, 1])
        label[x_grid, y_grid, 10+c-1] = 1
    return label


def pred2xywhcc(pred):
    # pred is a 7*7*(5*B+C) tensor
    bboxs = torch.zeros((7*7*2, 5+10))  # 98*15
    for x in range(7):
        for y in range(7):
            # bbox1
            bboxs[2*(x*7+y), 0:4] = torch.Tensor(
                [(pred[x, y, 0]+x)/7, (pred[x, y, 1]+y)/7, pred[x, y, 2], pred[x, y, 3]])
            bboxs[2*(x*7+y), 4] = pred[x, y, 4]
            bboxs[2*(x*7+y), 5:] = pred[x, y, 10:]

            # bbox2
            bboxs[2*(x*7+y)+1, 0:4] = torch.Tensor(
                [(pred[x, y, 5]+x)/7, (pred[x, y, 6]+y)/7, pred[x, y, 7], pred[x, y, 8]])
            bboxs[2*(x*7+y)+1, 4] = pred[x, y, 9]
            bboxs[2*(x*7+y)+1, 5:] = pred[x, y, 10:]
    # apply NMS to all bboxs
    xywhcc = nms(bboxs)
    return xywhcc


def nms(bboxs, conf_thresh=0.1, iou_thresh=0.3):
    # Non-Maximum Suppression, bboxs is a 98*15 tensor
    bbox_prob = bboxs[:, 5:].clone()  # 98*10
    bbox_conf = bboxs[:, 4].clone().unsqueeze(1).expand_as(bbox_prob)  # 98*10
    bbox_cls_spec_conf = bbox_conf*bbox_prob  # 98*10
    bbox_cls_spec_conf[bbox_cls_spec_conf <= conf_thresh] = 0

    # for each class, sort the cls-spec-conf score
    for c in range(10):
        rank = torch.sort(
            bbox_cls_spec_conf[:, c], descending=True).indices  # sort
        # for each bbox
        for i in range(98):
            if bbox_cls_spec_conf[rank[i], c] == 0:
                continue
            for j in range(i+1, 98):
                if bbox_cls_spec_conf[rank[j], c] != 0:
                    iou = calculate_iou(
                        bboxs[rank[i], 0:4], bboxs[rank[j], 0:4])
                    if iou > iou_thresh:
                        bbox_cls_spec_conf[rank[j], c] = 0

    # exclude cls-specific confidence score=0
    bboxs = bboxs[torch.max(bbox_cls_spec_conf, dim=1).values > 0]

    bbox_cls_spec_conf = bbox_cls_spec_conf[torch.max(
        bbox_cls_spec_conf, dim=1).values > 0]

    res = torch.ones((bboxs.size()[0], 6))

    # return null
    if bboxs.size()[0] == 0:
        return torch.tensor([])

    # bbox coord
    res[:, 0:4] = bboxs[:, 0:4]
    # bbox class-specific confidence scores
    res[:, 4] = torch.max(bbox_cls_spec_conf, dim=1).values
    # bbox class
    res[:, 5] = torch.argmax(bboxs[:, 5:], dim=1).int()
    return res


def calculate_iou(bbox1, bbox2):
    # bbox: x y w h
    bbox1, bbox2 = bbox1.cpu().detach().numpy(
    ).tolist(), bbox2.cpu().detach().numpy().tolist()

    area1 = bbox1[2]*bbox1[3]  # bbox1's area
    area2 = bbox2[2]*bbox2[3]  # bbox2's area

    max_left = max(bbox1[0]-bbox1[2]/2, bbox2[0]-bbox2[2]/2)
    min_right = min(bbox1[0]+bbox1[2]/2, bbox2[0]+bbox2[2]/2)
    max_top = max(bbox1[1]-bbox1[3]/2, bbox2[1]-bbox2[3]/2)
    min_bottom = min(bbox1[1]+bbox1[3]/2, bbox2[1]+bbox2[3]/2)

    if max_left >= min_right or max_top >= min_bottom:
        return 0
    else:
        # iou = intersect / union
        intersect = (min_right-max_left)*(min_bottom-max_top)
        return (intersect / (area1+area2-intersect))


class YOLOLoss(nn.Module):
    def __init__(self):
        super(YOLOLoss, self).__init__()

    def forward(self, preds, labels):
        batch_size = labels.size()[0]

        loss_coord_xy = 0  # coord xy loss
        loss_coord_wh = 0  # coord wh loss
        loss_obj = 0  # obj loss
        loss_noobj = 0  # noobj loss
        loss_class = 0  # class loss

        for n in range(batch_size):
            for x in range(7):
                for y in range(7):
                    # this region has object
                    if labels[n, x, y, 4] == 1:
                        # convert x,y to x,y
                        pred_bbox1 = torch.Tensor(
                            [(preds[n, x, y, 0]+x)/7, (preds[n, x, y, 1]+y)/7, preds[n, x, y, 2], preds[n, x, y, 3]])
                        pred_bbox2 = torch.Tensor(
                            [(preds[n, x, y, 5]+x)/7, (preds[n, x, y, 6]+y)/7, preds[n, x, y, 7], preds[n, x, y, 8]])
                        label_bbox = torch.Tensor(
                            [(labels[n, x, y, 0]+x)/7, (labels[n, x, y, 1]+y)/7, labels[n, x, y, 2], labels[n, x, y, 3]])

                        # calculate iou of two bbox
                        iou1 = calculate_iou(
                            pred_bbox1, label_bbox)
                        iou2 = calculate_iou(
                            pred_bbox2, label_bbox)

                        # judge responsible box
                        if iou1 > iou2:
                            # calculate coord xy loss
                            loss_coord_xy += 5 * \
                                torch.sum(
                                    (preds[n, x, y, 0:2] - labels[n, x, y, 0:2])**2)

                            # calculate coord wh loss
                            loss_coord_wh += torch.sum(
                                (preds[n, x, y, 2:4].sqrt()-labels[n, x, y, 2:4].sqrt())**2)

                            # calculate obj confidence loss
                            loss_obj += (preds[n, x, y, 4] - iou1)**2

                            # calculate noobj confidence loss
                            loss_noobj += 0.5 * ((preds[n, x, y, 9]-iou2)**2)
                        else:
                            # calculate coord xy loss
                            loss_coord_xy += 5 * \
                                torch.sum(
                                    (preds[n, x, y, 5:7] - labels[n, x, y, 5:7])**2)

                            # calculate coord wh loss
                            loss_coord_wh += torch.sum(
                                (preds[n, x, y, 7:9].sqrt()-labels[n, x, y, 7:9].sqrt())**2)

                            # calculate obj confidence loss
                            loss_obj += (preds[n, x, y, 9] - iou2)**2

                            # calculate noobj confidence loss
                            loss_noobj += 0.5 * ((preds[n, x, y, 4]-iou1)**2)

                        # calculate class loss
                        loss_class += torch.sum(
                            (preds[n, x, y, 10:] - labels[n, x, y, 10:])**2)

                    # this region has no object
                    else:
                        loss_noobj += 0.5 * \
                            torch.sum(preds[n, x, y, [4, 9]]**2)

                    # end labels have object
                # end for y
            # end for x
        # end for batchsize

        # print('loss_coord_xy', loss_coord_xy)
        # print('loss_coord_wh', loss_coord_wh)
        # print('loss_coord_wh', loss_obj)
        # print('loss_coord_wh', loss_noobj)
        # print('loss_coord_wh', loss_class)

        loss = loss_coord_xy + loss_coord_wh + loss_obj + \
            loss_noobj + loss_class  # five loss terms
        return loss/batch_size


def parse_cfg(cfg_path):
    cfg = {}
    with open(cfg_path, 'r') as f:
        lines = f.readlines()
        for line in lines:
            if line[0] == '#' or line == '\n':
                continue
            line = line.strip().split(':')
            key, value = line[0].strip(), line[1].strip()
            cfg[key] = value
    return cfg
